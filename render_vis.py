import sys, os
sys.path.append(os.path.dirname(os.path.dirname(sys.path[0])))
os.environ["PYOPENGL_PLATFORM"] = "egl" #opengl seems to only work with TPU
sys.path.insert(0,'third_party')

import subprocess
import imageio
import glob
import matplotlib.pyplot as plt
import numpy as np
import torch
import cv2
import pdb
import soft_renderer as sr
import argparse
import trimesh
from nnutils.geom_utils import obj_to_cam, pinhole_cam
import pyrender
from pyrender import IntrinsicsCamera,Mesh, Node, Scene,OffscreenRenderer
import configparser

parser = argparse.ArgumentParser(description='render mesh')
parser.add_argument('--testdir', default='',
                    help='path to test dir')
parser.add_argument('--seqname', default='camel',
                    help='sequence to test')
parser.add_argument('--freeze', default='no',
                    help='freeze object at frist frame')
parser.add_argument('--watertight', default='no',
                    help='watertight remesh')
parser.add_argument('--outpath', default='/data/gengshay/output.gif',
                    help='output path')
parser.add_argument('--overlay', default='yes',
                    help='whether to overlay with the input')
parser.add_argument('--fixlight', default='yes',
                    help='whether to fix the light direction')
parser.add_argument('--cam_type', default='perspective',
                    help='camera model, orthographic or perspective')
parser.add_argument('--vis_bones', default='no',
                    help='whether show transparent surface and vis bones')
parser.add_argument('--vis_traj', default='no',
                    help='whether show trajectory of vertices')
parser.add_argument('--append_img', default='no',
                    help='whether append images before the seq')
parser.add_argument('--append_render', default='yes',
                    help='whether append renderings')
parser.add_argument('--nosmooth', dest='smooth', action='store_false',
                    help='whether to smooth vertex colors and positions')
parser.add_argument('--corresp', dest='corresp', action='store_true',
                    help='whether to render correspondence')
parser.add_argument('--vp2', dest='vp2', action='store_true',
                    help='whether to render second viewpoint')
parser.add_argument('--vp3', dest='vp3', action='store_true',
                    help='whether to render third viewpoint')
parser.add_argument('--floor', dest='floor', action='store_true',
                    help='whether to add floor')
args = parser.parse_args()

def remesh(mesh):
    mesh.export('tmp/input.obj')
    print(subprocess.check_output(['./Manifold/build/manifold', 'tmp/input.obj', 'tmp/output.obj', '10000']))
    mesh = trimesh.load('tmp/output.obj',process=False)
    if args.overlay=='yes':
        mesh.visual.vertex_colors[:,1:3] = 0
        mesh.visual.vertex_colors[:,0] = 255
    return mesh

def main():
    print(args.testdir)
    # store all the data
    all_anno = []
    all_mesh = []
    all_bone = []
    all_cam = []
    all_fr = []

    targetstr = 'vp1pred'
    if args.corresp or args.vis_traj=='yes':targetstr = 'skinpred'
    if args.vp2:targetstr = 'vp2pred'
    if args.vp3:targetstr = 'vp3pred'
    
    config = configparser.RawConfigParser()
    config.read('configs/%s.config'%args.seqname)
    datapath = str(config.get('data', 'datapath'))
    init_frame = int(config.get('data', 'init_frame'))
    end_frame = int(config.get('data', 'end_frame'))
    dframe = int(config.get('data', 'dframe'))
    for name in sorted(glob.glob('%s/*'%datapath))[init_frame:end_frame][::dframe]:
        rgb_img = cv2.imread(name)
        sil_img = cv2.imread(name.replace('JPEGImages', 'Annotations').replace('.jpg', '.png'),0)[:,:,None]
        all_anno.append([rgb_img,sil_img,0,0,name])
        seqname = name.split('/')[-2]
        fr = int(name.split('/')[-1].split('.')[-2])
        all_fr.append(fr)
        print('%s/%d'%(seqname, fr))

        try:
            mesh = trimesh.load('%s/%s-%s%d.obj'%(args.testdir, args.seqname, targetstr, fr),process=False)
            cam = np.loadtxt('%s/%s-cam%d.txt'%(args.testdir,args.seqname,fr))
            #trimesh.repair.fix_inversion(mesh)
            if args.watertight=='yes':
                mesh = remesh(mesh) 
            all_mesh.append(mesh)
            all_cam.append(cam)
            all_bone.append(trimesh.load('%s/%s-gauss%d.obj'%(args.testdir, args.seqname,fr),process=False))
        except: print('no mesh found')

    # add bones?
    num_original_verts = []
    num_original_faces = []
    sphere_trajs = [] # K samples over T
    num_trajs = 0
    pts_trajs = []
    col_trajs = []
    traj_len = 5
    traj_num = len(all_mesh[0].vertices)

    if args.vis_traj=='yes':
        for i in range(len(all_mesh)):
            pts_traj = np.zeros((traj_len, traj_num,2,3))
            col_traj = np.zeros((traj_len, traj_num,2,4))
            for j in range(traj_len):
                if i-j-1<0: continue
                pts_traj[j,:,0] = all_mesh[i-j-1].vertices
                pts_traj[j,:,1] = all_mesh[i-j].vertices
                col_traj[j,:,0] = all_mesh[i-j-1].visual.vertex_colors/255
                col_traj[j,:,1] = all_mesh[i-j].visual.vertex_colors/255
            pts_trajs.append(pts_traj)
            col_trajs.append(col_traj)
    
    for i in range(len(all_mesh)):
        if args.vis_bones=='yes':
            all_mesh[i].visual.vertex_colors[:,-1]=192 # alpha
            num_original_verts.append( all_mesh[i].vertices.shape[0])
            num_original_faces.append( all_mesh[i].faces.shape[0]  )  
            all_mesh[i] = trimesh.util.concatenate([all_mesh[i], all_bone[i]])
        elif args.vis_traj=='yes':

            sphere = trimesh.creation.uv_sphere(radius=0.02,count=(3,3))
            sphere_traj = trimesh.Trimesh()
            ipts=0
            nverts = all_mesh[i].vertices.shape[0]

            sphere_trajs.append(sphere_traj)

            all_mesh[i] = remesh(all_mesh[i])
            all_mesh[i].visual.vertex_colors[:,:]=128 # rgb alpha
            num_original_verts.append( all_mesh[i].vertices.shape[0])
            num_original_faces.append( all_mesh[i].faces.shape[0]  )  
            all_mesh[i] = trimesh.util.concatenate([all_mesh[i], sphere_trajs[i]])

            
            
    # store all the results
    input_size = all_anno[0][0].shape[:2]
    if '.gif' in args.outpath:
        output_size = (int(input_size[0] * 480/input_size[1]), 480)# 270x480
    else:
        output_size = (int(input_size[0] * 960/input_size[1]), 960)# 540x960
    frames=[]
    if args.append_img=="yes":
        if args.append_render=='yes':
            if args.freeze=='yes': napp_fr = 30
            else:                  napp_fr = int(len(all_anno)//5)
            for i in range(napp_fr):
                frames.append(cv2.resize(all_anno[0][0],output_size[::-1])[:,:,::-1])
        else:
            for i in range(len(all_anno)):
                silframe=cv2.resize((all_anno[i][1]>0).astype(float),output_size[::-1])*255
                imgframe=cv2.resize(all_anno[i][0],output_size[::-1])[:,:,::-1]
                redframe=(np.asarray([1,0,0])[None,None] * silframe[:,:,None]).astype(np.uint8)
                imgframe = cv2.addWeighted(imgframe, 1, redframe, 0.5, 0)
                frames.append(imgframe)
    theta = 9*np.pi/9
    init_light_pose = np.asarray([[1,0,0,0],[0,np.cos(theta),-np.sin(theta),0],[0,np.sin(theta),np.cos(theta),0],[0,0,0,1]])
    init_light_pose0 =np.asarray([[1,0,0,0],[0,0,-1,0],[0,1,0,0],[0,0,0,1]])
    if args.freeze=='yes':
        size = 150
    else:
        size = len(all_anno)
    for i in range(size):
        if args.append_render=='no':break
        # render flow between mesh 1 and 2
        if args.freeze=='yes':
            print(i)
            refimg, refsil, refkp, refvis, refname = all_anno[0]
            img_size = max(refimg.shape)
            refmesh = all_mesh[0]
            refmesh.vertices -= refmesh.vertices.mean(0)[None]
            refmesh.vertices /= 1.2*np.abs(refmesh.vertices).max()
            refcam = all_cam[0].copy()
            refcam[:3,:3] = refcam[:3,:3].dot(cv2.Rodrigues(np.asarray([0.,-i*2*np.pi/size,0.]))[0])
            refcam[:2,3] = 0  # trans xy
            refcam[2,3] = 20 # depth
            if args.cam_type=='perspective':
                refcam[3,2] = refimg.shape[1]/2 # px py
                refcam[3,3] = refimg.shape[0]/2 # px py
                refcam[3,:2] = 8*img_size/2 # fl
            else:
                refcam[3,2] = refimg.shape[1]/2 # px py
                refcam[3,3] = refimg.shape[0]/2 # px py
                refcam[3,:2] =0.5 * img_size/2 # fl
        else:
            refimg, refsil, refkp, refvis, refname = all_anno[i]
            print('%s'%(refname))
            img_size = max(refimg.shape)
            refmesh = all_mesh[i]
            refcam = all_cam[i]
        currcam = np.concatenate([refcam[:3,:4],np.asarray([[0,0,0,1]])],0)
        if i==0:
            initcam = currcam.copy()
        
        refface = torch.Tensor(refmesh.faces[None]).cuda()
        verts = torch.Tensor(refmesh.vertices[None]).cuda()
        Rmat =  torch.Tensor(refcam[None,:3,:3]).cuda()
        Tmat =  torch.Tensor(refcam[None,:3,3]).cuda()
        ppoint =refcam[3,2:]
        scale = refcam[3,:2]
        verts = obj_to_cam(verts, Rmat, Tmat,nmesh=1,n_hypo=1,skin=None)
        if args.cam_type != 'perspective':
            scale = scale / img_size * 2
            ppoint = ppoint / img_size * 2 - 1
            verts[:,:,0] = ppoint[0]+verts[:,:, 0]*scale[0]
            verts[:,:,1] = ppoint[1]+verts[:,:, 1]*scale[1]
            verts[:,:,2] += (5+verts[:,:,2].min())

        r = OffscreenRenderer(img_size, img_size)
        colors = refmesh.visual.vertex_colors
        if args.watertight=='yes' or args.vis_traj: # shape  rendering
            scene = Scene(ambient_light=0.4*np.asarray([1.,1.,1.,1.]))
            direc_l = pyrender.DirectionalLight(color=np.ones(3), intensity=6.0)
            colors= np.concatenate([0.6*colors[:,:3].astype(np.uint8), colors[:,3:]],-1)  # avoid overexposure
        else:
            scene = Scene(ambient_light=0.7*np.asarray([1.,1.,1.,1.]))
            direc_l = pyrender.DirectionalLight(color=np.ones(3), intensity=0.0)
            colors= np.concatenate([colors[:,:3].astype(np.uint8), colors[:,3:]],-1)  # avoid overexposure


        smooth=args.smooth
        if args.freeze=='yes':
            tbone = 0
        else:
            tbone = i
        if args.vis_bones=='yes':
            mesh2 = trimesh.Trimesh(vertices=np.asarray(verts[0,num_original_verts[tbone]:,:3].cpu()), faces=np.asarray(refface[0,num_original_faces[tbone]:].cpu()-num_original_verts[tbone]),vertex_colors=colors[num_original_verts[tbone]:])
            mesh2=Mesh.from_trimesh(mesh2,smooth=smooth)
            mesh2._primitives[0].material.RoughnessFactor=.5
            scene.add_node( Node(mesh=mesh2))
        elif args.vis_traj=='yes':
            mesh = trimesh.Trimesh(vertices=np.asarray(verts[0,:num_original_verts[tbone],:3].cpu()), faces=np.asarray(refface[0,:num_original_faces[tbone]].cpu()),vertex_colors=colors[:num_original_verts[tbone]])
            meshr = Mesh.from_trimesh(mesh,smooth=smooth)
            meshr._primitives[0].material.RoughnessFactor=.5
            scene.add_node( Node(mesh=meshr ))
            pts = pts_trajs[i].reshape(-1,3)# np.asarray([[-1,-1,1],[1,1,1]])  # 2TxNx3
            colors = col_trajs[i].reshape(-1,4)#np.random.uniform(size=pts.shape)
            m = Mesh([pyrender.Primitive(pts,mode=1,color_0=colors)])
            scene.add_node( Node(mesh=m)) 
        else: 
            mesh = trimesh.Trimesh(vertices=np.asarray(verts[0,:,:3].cpu()), faces=np.asarray(refface[0].cpu()),vertex_colors=colors)
            meshr = Mesh.from_trimesh(mesh,smooth=smooth)
            meshr._primitives[0].material.RoughnessFactor=.5
            scene.add_node( Node(mesh=meshr ))

        floor_mesh = trimesh.load('./database/misc/wood.obj',process=False)
        floor_mesh.vertices = np.concatenate([floor_mesh.vertices[:,:1], floor_mesh.vertices[:,2:3], floor_mesh.vertices[:,1:2]],-1 )
        xfloor = 10*mesh.vertices[:,0].min() + (10*mesh.vertices[:,0].max()-10*mesh.vertices[:,0].min())*(floor_mesh.vertices[:,0:1] - floor_mesh.vertices[:,0].min())/(floor_mesh.vertices[:,0].max()-floor_mesh.vertices[:,0].min()) 
        yfloor = floor_mesh.vertices[:,1:2]; yfloor[:] = (mesh.vertices[:,1].max())
        zfloor = 0.5*mesh.vertices[:,2].min() + (10*mesh.vertices[:,2].max()-0.5*mesh.vertices[:,2].min())*(floor_mesh.vertices[:,2:3] - floor_mesh.vertices[:,2].min())/(floor_mesh.vertices[:,2].max()-floor_mesh.vertices[:,2].min())
        floor_mesh.vertices = np.concatenate([xfloor,yfloor,zfloor],-1)
        floor_mesh = trimesh.Trimesh(floor_mesh.vertices, floor_mesh.faces, vertex_colors=255*np.ones((4,4), dtype=np.uint8))
        if args.floor:
            scene.add_node( Node(mesh=Mesh.from_trimesh(floor_mesh))) # overrides the prev. one
       
        if args.cam_type=='perspective': 
            cam = IntrinsicsCamera(
                    scale[0],
                    scale[0],
                    ppoint[0],
                    ppoint[1],
                    znear=1e-3,zfar=1000)
        else:
            cam = pyrender.OrthographicCamera(xmag=1., ymag=1.)
        cam_pose = -np.eye(4); cam_pose[0,0]=1; cam_pose[-1,-1]=1
        cam_node = scene.add(cam, pose=cam_pose)
        if args.fixlight == 'yes':
            light_pose = (np.linalg.inv(currcam).dot(initcam)).dot(init_light_pose)
        else:
            light_pose = init_light_pose
        direc_l_node = scene.add(direc_l, pose=light_pose)
        if args.vis_bones=='yes' or args.vis_traj=='yes':
            color, depth = r.render(scene,flags=pyrender.RenderFlags.SHADOWS_DIRECTIONAL)
        else:
            color, depth = r.render(scene,flags=pyrender.RenderFlags.SHADOWS_DIRECTIONAL | pyrender.RenderFlags.SKIP_CULL_FACES)
        r.delete()
        color = color[:refimg.shape[0],:refimg.shape[1],:3]
        if args.overlay=='yes':
            color = cv2.addWeighted(color, 0.5, refimg[:,:,::-1], 0.5, 0)
        prefix = (args.outpath).split('/')[-1].split('.')[0]
        color = color.copy(); color[0,0,:] = 0
        imoutpath = '%s/%s-mrender%03d.png'%(args.testdir, prefix,i)
        if args.vp2: imoutpath = imoutpath.replace('mrender', 'vp2-mrender')
        if args.vp3: imoutpath = imoutpath.replace('mrender', 'vp3-mrender')
        cv2.imwrite(imoutpath,color[:,:,::-1] )
        color = cv2.resize(color, output_size[::-1])

        frames.append(color)
    imageio.mimsave('%s'%args.outpath, frames, fps=1./(5./len(frames)))
if __name__ == '__main__':
    main()
