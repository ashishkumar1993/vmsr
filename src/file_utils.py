# Copyright 2016 The TensorFlow Authors All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

"""Utilities for manipulating files.
"""
import os
import PIL
import cv2, shutil

exists   = os.path.exists
fopen    = lambda path, mode: open(path, mode)
makedirs = os.makedirs
listdir  = os.listdir
copyfile = shutil.copyfile

# def write_image(image_path, rgb):
#   ext = os.path.splitext(image_path)[1]
#   with gfile.GFile(image_path, 'w') as f:
#     img_str = cv2.imencode(ext, rgb[:,:,::-1])[1].tostring()
#     f.write(img_str)
# 
# def read_image(image_path, type='rgb'):
#   with fopen(file_name, 'r') as f:
#     I = PIL.Image.open(f)
#     II = np.array(I)
#     if type == 'rgb':
#       II = II[:,:,:3]
#   return II
