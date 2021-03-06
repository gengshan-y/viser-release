# Copyright 2021 Google LLC
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#     https://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import pdb
import os.path as osp
from absl import flags, app
import time
import sys
sys.path.insert(0,'third_party')

import torch
from scipy.ndimage import binary_erosion
import numpy as np
from torch.utils.data import Dataset
from torch.utils.data import DataLoader
from torch.utils.data.dataloader import default_collate
import cv2

from ext_utils import image as image_utils
from ext_utils.util_flow import readPFM
from ext_utils.flowlib import warp_flow

# -------------- Dataset ------------- #
# ------------------------------------ #
class BaseDataset(Dataset):
    ''' 
    img, mask, flow data loader
    '''

    def __init__(self, opts, filter_key=None):
        self.opts = opts
        self.img_size = opts.img_size
    def rot_augment(self,x0):
        #if np.random.rand(1) > self.random_geo:
        if False:
            # centralize
            A0 = np.asarray([[1,0,x0.shape[0]/2.],
                            [0,1,x0.shape[1]/2.],
                            [0,0,1]])
            # rotate
            A = cv2.Rodrigues(np.asarray([0.,0.,2.]))[0]
            A = A0.dot(A).dot(np.linalg.inv(A0))
            A = A.T
        else:
            A = np.eye(3)
        return A
    def geo_augment(self,x0):
        if False:
            # mirror
            A = np.asarray([[-1,0,x0.shape[0]-1],
                            [0,1,0             ],
                            [0,0,1]]).T
        else:
            A = np.eye(3)
        Ap = A

        A = A.dot(self.rot_augment(x0))
        Ap=Ap.dot(self.rot_augment(x0))

        return A,Ap
    
    def __len__(self):
        return self.num_imgs

    def __getitem__(self, index):
        # TODO redirect index to the current list of images
        im0idx = self.baselist[index]
        direction = self.directlist[index]
        im0idx = self.start_idx + \
            im0idx / (len(self.imglist)-1) * (self.end_idx - self.start_idx)
        im0idx = int(im0idx)
        equal_list = np.abs(np.asarray(self.baselist) - im0idx)
        equal_list = np.where(equal_list==equal_list.min())[0] 
        if len(equal_list)>1:
            index = sorted(equal_list)[1-direction]
        else:
            index = equal_list[0]

        #TODO: chech if this works for a fac*baselist
        im0idx = self.baselist[index]
        im1idx = im0idx + self.dframe if self.directlist[index]==1 else im0idx-self.dframe
        img_path = self.imglist[im0idx]
        img = cv2.imread(img_path)[:,:,::-1] / 255.0

        img_path = self.imglist[im1idx]
        imgn = cv2.imread(img_path)[:,:,::-1] / 255.0
        # Some are grayscale:
        shape = img.shape
        if len(shape) == 2:
            img = np.repeat(np.expand_dims(img, 2), 3, axis=2)
            imgn = np.repeat(np.expand_dims(imgn, 2), 3, axis=2)

        mask = cv2.imread(self.masklist[im0idx],0)
        mask = mask/np.sort(np.unique(mask))[1]
        occluder = mask==255
        mask[occluder] = 0
        if mask.shape[0]!=img.shape[0] or mask.shape[1]!=img.shape[1]:
            mask = cv2.resize(mask, img.shape[:2][::-1],interpolation=cv2.INTER_NEAREST)
            mask = binary_erosion(mask,iterations=2)
        mask = np.expand_dims(mask, 2)

        maskn = cv2.imread(self.masklist[im1idx],0)
        maskn = maskn/np.sort(np.unique(maskn))[1]
        occludern = maskn==255
        maskn[occludern] = 0
        if maskn.shape[0]!=imgn.shape[0] or maskn.shape[1]!=imgn.shape[1]:
            maskn = cv2.resize(maskn, imgn.shape[:2][::-1],interpolation=cv2.INTER_NEAREST)
            maskn = binary_erosion(maskn,iterations=1)
        maskn = np.expand_dims(maskn, 2)
        
        # complement color
        color = 1-img[mask[:,:,0].astype(bool)].mean(0)[None,None,:]
        colorn = 1-imgn[maskn[:,:,0].astype(bool)].mean(0)[None,None,:]
        img =   img*(mask>0).astype(float)    + color  *(1-(mask>0).astype(float))
        imgn =   imgn*(maskn>0).astype(float) + colorn *(1-(maskn>0).astype(float))

        # flow
        if self.directlist[index]==1:
            flowpath = self.flowfwlist[im0idx]
            flowpathn =self.flowbwlist[im0idx+self.dframe]
        else:
            flowpath = self.flowbwlist[im0idx]
            flowpathn =self.flowfwlist[im0idx-self.dframe]
        flow = readPFM(flowpath)[0]
        flown =readPFM(flowpathn)[0]
        occ = readPFM(flowpath.replace('flo-', 'occ-'))[0]
        occn =readPFM(flowpathn.replace('flo-', 'occ-'))[0]

        # resize flow
        h,w,_ = mask.shape
        oh,ow=flow.shape[:2]
        factor_h = h/oh
        factor_w = w/ow
        flow = cv2.resize(flow, (w,h))
        occ  = cv2.resize(occ, (w,h))
        flow[...,0] *= factor_w
        flow[...,1] *= factor_h
        
        h,w,_ = maskn.shape
        oh,ow=flown.shape[:2]
        factor_h = h/oh
        factor_w = w/ow
        flown = cv2.resize(flown, (w,h))
        occn  = cv2.resize(occn, (w,h))
        flown[...,0] *= factor_w
        flown[...,1] *= factor_h

        occ[occluder] = 0
        occn[occludern] = 0
        #print('time: %f'%(time.time()-ss))
       
        # crop box
        indices = np.where(mask>0); xid = indices[1]; yid = indices[0]
        indicesn = np.where(maskn>0); xidn = indicesn[1]; yidn = indicesn[0]
        center = ( (xid.max()+xid.min())//2, (yid.max()+yid.min())//2)
        centern = ( (xidn.max()+xidn.min())//2, (yidn.max()+yidn.min())//2)
        length = ( (xid.max()-xid.min())//2, (yid.max()-yid.min())//2)
        lengthn = ( (xidn.max()-xidn.min())//2, (yidn.max()-yidn.min())//2)

        length = (int(1.2*length[0]), int(1.2*length[1]))
        lengthn= (int(1.2*lengthn[0]),int(1.2*lengthn[1]))

        maxw=self.opts.img_size;maxh=self.opts.img_size
        orisize = (2*length[0], 2*length[1])
        orisizen= (2*lengthn[0], 2*lengthn[1])
        alp =  [orisize[0]/maxw  ,orisize[1]/maxw]
        alpn = [orisizen[0]/maxw ,orisizen[1]/maxw]
        x0,y0  =np.meshgrid(range(maxw),range(maxh))
        # geometric augmentation for img, mask, flow, occ
        A,Ap = self.geo_augment(x0)
        B = np.asarray([[alp[0],0,(center[0]-length[0])],
                        [0,alp[1],(center[1]-length[1])],
                        [0,0,1]]).T
        Bp= np.asarray([[alpn[0],0,(centern[0]-lengthn[0])],
                        [0,alpn[1],(centern[1]-lengthn[1])],
                        [0,0,1]]).T
        
        hp0 = np.stack([x0,y0,np.ones_like(x0)],-1)  # screen coord
        hp1 = np.stack([x0,y0,np.ones_like(x0)],-1)  # screen coord
        hp0 = np.dot(hp0,A).dot(B)                   # image coord
        hp1 = np.dot(hp1,Ap).dot(Bp)                  # image coord
        x0 = hp0[:,:,0].astype(np.float32)
        y0 = hp0[:,:,1].astype(np.float32)
        x0n = hp1[:,:,0].astype(np.float32)
        y0n = hp1[:,:,1].astype(np.float32)
        
        img = cv2.remap(img,x0,y0,interpolation=cv2.INTER_LINEAR,borderValue=color[0,0])
        mask = cv2.remap(mask.astype(int),x0,y0,interpolation=cv2.INTER_NEAREST)
        flow = cv2.remap(flow,x0,y0,interpolation=cv2.INTER_LINEAR)
        occ = cv2.remap(occ,x0,y0,interpolation=cv2.INTER_LINEAR)
        

        imgn = cv2.remap(imgn,x0n,y0n,interpolation=cv2.INTER_LINEAR,borderValue=colorn[0,0])
        maskn = cv2.remap(maskn.astype(int),x0n,y0n,interpolation=cv2.INTER_NEAREST)
        flown = cv2.remap(flown,x0n,y0n,interpolation=cv2.INTER_LINEAR)
        occn = cv2.remap(occn,x0n,y0n,interpolation=cv2.INTER_LINEAR)

        # augmenta flow
        hp1c = np.concatenate([flow[:,:,:2] + hp0[:,:,:2], np.ones_like(hp0[:,:,:1])],-1) # image coord
        hp1c = hp1c.dot(np.linalg.inv(Ap.dot(Bp)))   # screen coord
        flow[:,:,:2] = hp1c[:,:,:2] - np.stack(np.meshgrid(range(maxw),range(maxh)),-1)
        
        hp0c = np.concatenate([flown[:,:,:2] +hp1[:,:,:2], np.ones_like(hp0[:,:,:1])],-1) # image coord
        hp0c = hp0c.dot(np.linalg.inv(A.dot(B)))   # screen coord
        flown[:,:,:2] =hp0c[:,:,:2] - np.stack(np.meshgrid(range(maxw),range(maxh)),-1)

        #fb check
        x0,y0  =np.meshgrid(range(maxw),range(maxh))
        hp0 = np.stack([x0,y0,np.ones_like(x0)],-1)  # screen coord

        dis = warp_flow(hp0 + flown, flow[:,:,:2]) - hp0
        dis = np.linalg.norm(dis[:,:,:2],2,-1) * 0.1
        occ[occ!=0] = dis[occ!=0]

        disn = warp_flow(hp0 + flow, flown[:,:,:2]) - hp0
        disn = np.linalg.norm(disn[:,:,:2],2,-1) * 0.1
        occn[occn!=0] = disn[occn!=0]

        # ndc
        flow[:,:,0] = 2 * (flow[:,:,0]/maxw)
        flow[:,:,1] = 2 * (flow[:,:,1]/maxh)
        flow[:,:,2] = np.logical_and(flow[:,:,2]!=0, occ<10)  # as the valid pixels
        flown[:,:,0] = 2 * (flown[:,:,0]/maxw)
        flown[:,:,1] = 2 * (flown[:,:,1]/maxh)
        flown[:,:,2] = np.logical_and(flown[:,:,2]!=0, occn<10)  # as the valid pixels

        # Finally transpose the image to 3xHxW
        img = np.transpose(img, (2, 0, 1))
        mask = (mask>0).astype(float)
        
        imgn = np.transpose(imgn, (2, 0, 1))
        maskn = (maskn>0).astype(float)
        flow = np.transpose(flow, (2, 0, 1))
        flown = np.transpose(flown, (2, 0, 1))
            
        cam = np.zeros((7,))
        cam = np.asarray([1.,0.,0. ,1.,0.,0.,0.])
        camn = np.asarray([1.,0.,0. ,1.,0.,0.,0.])
        # correct cx,cy at clip space (not tx, ty)
        pps  = np.asarray([float( center[0] - length[0] ), float( center[1] - length[1]  )])
        ppsn = np.asarray([float( centern[0]- lengthn[0]), float(centern[1] - lengthn[1] )])
        cam[:1]=1./alp[0]   # modify focal length according to rescale
        camn[:1]=1./alpn[0]
        cam[1:2]=1./alp[1]   # modify focal length according to rescale
        camn[1:2]=1./alpn[1]

        # compute transforms
        mask = np.stack([mask,maskn])
        dmask_dts =  np.stack([image_utils.compute_dt(m, iters=10) for m in mask])

        try:dataid = self.dataid
        except: dataid=0

        # remove background
        elem = {
            'img': img,
            'mask': mask,
            'dmask_dts': dmask_dts,
            'cam': cam,
            'inds': index,

            'imgn': imgn,
            'camn': camn,
            'indsn': index,
            'flow': flow,
            'flown': flown,

            'pps': np.stack([pps,ppsn]),

            'is_canonical':  self.can_frame == im0idx,
            'is_canonicaln': self.can_frame == im1idx,
            'dataid': dataid,        

            'id0': im0idx,
            'id1': im1idx,

            'occ': occ,
            'occn': occn,  # out-of-range score; 0: border

            'shape': np.asarray(shape)[:2][::-1].copy(),
            }
        # add RTK: [R_3x3|T_3x1]
        #          [fx,fy,px,py], to the ndc space
        try:
            rtk_path = self.rtklist[im0idx]
            rtkn_path = self.rtklist[im1idx]
            rtk = np.loadtxt(rtk_path)
            rtkn = np.loadtxt(rtkn_path)
        except:
            print('warning: loading empty camera')
            print(rtk_path)
            rtk = np.zeros((4,4))
            rtk[:3,:3] = np.eye(3)
            rtk[:3, 3] = np.asarray([0,0,10])
            rtk[3, :]  = np.asarray([512,512,256,256])  
            rtkn = rtk

        elem['rtk']  = rtk
        elem['rtkn'] = rtkn
        return elem
