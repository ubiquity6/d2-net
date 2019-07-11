from os import listdir
from os.path import isfile, join

# Utility for writing list of images to process

folders = ["~/aachen/images/images_upright/db/", 
           "~/aachen/images/images_upright/query/night/nexus5x/", 
           "~/aachen/images/images_upright/query/night/milestone/", 
           "~/aachen/images/images_upright/query/day/nexus5x/", 
           "~/aachen/images/images_upright/query/day/nexus4/", 
           "~/aachen/images/images_upright/query/day/milestone/"]
files = []
for folder in folders:
    files.extend([join(folder, f) for f in listdir(folder) if isfile(join(folder, f)) and f.endswith('.jpg')])

with open('images.txt', 'w') as image_list_file:
    for f in files:
        image_list_file.write(f + '\n')