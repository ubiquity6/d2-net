import argparse

import numpy as np

import os

import shutil

import torch
import torch.optim as optim

from torch.utils.data import DataLoader

from tqdm import tqdm

import warnings

from lib.dataset import MegaDepthDataset
from lib.exceptions import NoGradientError
from lib.loss import loss_function
from lib.model import D2Net

# Inside my model training code
import time
import wandb
wandb.init(
    project="d2-net-ots",
    name="d2-net MD test",
)

# Ignore EXIF warnings
warnings.filterwarnings("ignore", "Corrupt EXIF data", UserWarning)
warnings.filterwarnings("ignore", "Possibly corrupt EXIF data", UserWarning)

# CUDA
use_cuda = torch.cuda.is_available()
device = torch.device("cuda:0" if use_cuda else "cpu")

# Seed
torch.manual_seed(1)
if use_cuda:
    torch.cuda.manual_seed(1)
np.random.seed(1)

# Argument parsing
parser = argparse.ArgumentParser(description='Training script')

parser.add_argument(
    '--dataset_path', type=str, required=True,
    help='path to the dataset'
)
parser.add_argument(
    '--scene_info_path', type=str, required=True,
    help='path to the processed scenes'
)

parser.add_argument(
    '--preprocessing', type=str, default='caffe',
    help='image preprocessing (caffe or torch)'
)
parser.add_argument(
    '--model_file', type=str, default='models/d2_ots.pth',
    help='path to the full model'
)

parser.add_argument(
    '--num_epochs', type=int, default=50,
    help='number of training epochs'
)
parser.add_argument(
    '--lr', type=float, default=1e-3,
    help='initial learning rate'
)
parser.add_argument(
    '--batch_size', type=int, default=1,
    help='batch size'
)
parser.add_argument(
    '--num_workers', type=int, default=8,
    help='number of workers for data loading'
)

parser.add_argument(
    '--use_validation', dest='use_validation', action='store_true',
    help='use the validation split'
)
parser.set_defaults(use_validation=False)

parser.add_argument(
    '--log_interval', type=int, default=500,
    help='loss logging interval'
)
parser.add_argument(
    '--log_file', type=str, default='log.txt',
    help='loss logging file'
)

# Checkpoints are saved in wandb run dir

parser.add_argument(
    '--checkpoint_prefix', type=str, default='d2',
    help='prefix for training checkpoints'
)

args = parser.parse_args()

print(args)

wandb.config.update(args)

# Creating CNN model
model = D2Net(
    model_file=args.model_file,
    use_cuda=use_cuda
)

wandb.watch(model)

# Optimizer
optimizer = optim.Adam(
    filter(lambda p: p.requires_grad, model.parameters()), lr=args.lr
)
scheduler = optim.lr_scheduler.ReduceLROnPlateau(
    optimizer, factor=0.1, patience=2, verbose=True
)

# Dataset
if args.use_validation:
    validation_dataset = MegaDepthDataset(
        scene_list_path='megadepth_utils/valid_scenes.txt',
        scene_info_path=args.scene_info_path,
        base_path=args.dataset_path,
        train=False,
        preprocessing=args.preprocessing,
        pairs_per_scene=25
    )
    validation_dataloader = DataLoader(
        validation_dataset,
        batch_size=args.batch_size,
        num_workers=args.num_workers
    )

training_dataset = MegaDepthDataset(
    scene_list_path='megadepth_utils/train_scenes.txt',
    scene_info_path=args.scene_info_path,
    base_path=args.dataset_path,
    preprocessing=args.preprocessing
)
training_dataloader = DataLoader(
    training_dataset,
    batch_size=args.batch_size,
    num_workers=args.num_workers
)


# Define epoch function
def process_epoch(
        epoch_idx,
        model, loss_function, optimizer, dataloader, device,
        log_file, args, train=True
):
    epoch_losses = []

    torch.set_grad_enabled(train)

    progress_bar = tqdm(enumerate(dataloader), total=len(dataloader))
    time_epoch_start = time.time()
    for batch_idx, batch in progress_bar:
        if train:
            optimizer.zero_grad()

        batch['train'] = train
        batch['epoch_idx'] = epoch_idx
        batch['batch_idx'] = batch_idx
        batch['batch_size'] = args.batch_size
        batch['preprocessing'] = args.preprocessing
        batch['log_interval'] = args.log_interval

        try:
            loss = loss_function(model, batch, device)
        except NoGradientError:
            continue

        current_loss = loss.data.cpu().numpy()[0]
        epoch_losses.append(current_loss)

        progress_bar.set_postfix(loss=('%.4f' % np.mean(epoch_losses)))

        if batch_idx % args.log_interval == 0:

            log_file.write('[%s] epoch %d - batch %d / %d - avg_loss: %f\n' % (
                'train' if train else 'valid',
                epoch_idx, batch_idx, len(dataloader), np.mean(epoch_losses)
            ))
            wandb.log({'epoch': epoch_idx, 'batch': batch_idx, 'avg_loss': np.mean(epoch_losses)}, commit=False)

        if train:
            loss.backward()
            optimizer.step()
    duration = time.time() - time_epoch_start
    avg_loss = np.mean(epoch_losses)
    wandb.log({'epoch': epoch_idx, 'avg_loss': avg_loss, 'train': train, 'time': duration}, step=epoch_idx)

    log_file.write('[%s] epoch %d - avg_loss: %f\n' % (
        'train' if train else 'valid',
        epoch_idx,
        avg_loss
    ))
    log_file.flush()

    return avg_loss

# Open the log file for writing
if os.path.exists(args.log_file):
    print('[Warning] Log file already exists.')
log_file = open(args.log_file, 'a+')

# Initialize the history
train_loss_history = []
validation_loss_history = []
if args.use_validation:
    validation_dataset.build_dataset()
    min_validation_loss = process_epoch(
        0,
        model, loss_function, optimizer, validation_dataloader, device,
        log_file, args,
        train=False
    )

# Start the training
for epoch_idx in range(1, args.num_epochs + 1):
    # Process epoch
    training_dataset.build_dataset()
    train_loss_history.append(
        process_epoch(
            epoch_idx,
            model, loss_function, optimizer, training_dataloader, device,
            log_file, args
        )
    )

    if args.use_validation:
        validation_loss_history.append(
            process_epoch(
                epoch_idx,
                model, loss_function, optimizer, validation_dataloader, device,
                log_file, args,
                train=False
            )
        )
        # Update LR if need be
        scheduler.step(validation_loss_history[-1])

    # Save the current checkpoint
    checkpoint_path = os.path.join(
        wandb.run.dir,
        '%s.%02d.pth' % (args.checkpoint_prefix, epoch_idx)
    )
    checkpoint = {
        'args': args,
        'epoch_idx': epoch_idx,
        'model': model.state_dict(),
        'optimizer': optimizer.state_dict(),
        'train_loss_history': train_loss_history,
        'validation_loss_history': validation_loss_history
    }
    torch.save(checkpoint, checkpoint_path)
    if (
        args.use_validation and
        validation_loss_history[-1] < min_validation_loss
    ):
        min_validation_loss = validation_loss_history[-1]
        best_checkpoint_path = os.path.join(
            wandb.run.dir,
            '%s.best.pth' % args.checkpoint_prefix
        )
        shutil.copy(checkpoint_path, best_checkpoint_path)

# Close the log file
log_file.close()
