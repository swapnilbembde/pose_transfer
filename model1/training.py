import torch
import torch.nn as nn
from torchvision import transforms, utils
import torch.utils.data as Data
from torch.autograd import Variable
from torch.optim import lr_scheduler
from config import cfg


from tensorboardX import SummaryWriter
import os, itertools
import dataset
import time
import matplotlib.pyplot as plt

from unet import UNet
import torch.nn.functional as F
import torch.backends.cudnn as cudnn

from collections import OrderedDict

class discriminator(nn.Module):
    def __init__(self, d=128):
        super(discriminator, self).__init__()

        self.conv1 = nn.Conv2d(9, d, 4, 2, 1)
        self.conv1_bn = nn.BatchNorm2d(d)

        self.conv2 = nn.Conv2d(d, d*2, 4, 2, 1)
        self.conv2_bn = nn. BatchNorm2d(d*2)

        self.conv3 = nn.Conv2d(d*2, d*4, 4, 2, 1)
        self.conv3_bn = nn. BatchNorm2d(d*4)

        self.conv4 = nn.Conv2d(d*4, d*8, 4, 2, 1)
        self.conv4_bn = nn. BatchNorm2d(d*8)

        self.conv5 = nn.Conv2d(d*8, d*8, 4, 2, 1)
        self.conv5_bn = nn. BatchNorm2d(d*8)

        # self.conv6 = nn.Conv2d(d*8, d*8, 4, 2, 1)
        # self.conv6_bn = nn. BatchNorm2d(d*8)

        self.conv7 = nn.Conv2d(d*8, 1, 4, 2, 0)

    def forward(self, s, img):
        inp = torch.cat([s, img], 1)
        inp = F.leaky_relu((self.conv1(inp)), 0.2)
        inp = F.leaky_relu(self.conv2_bn(self.conv2(inp)), 0.2)
        inp = F.leaky_relu(self.conv3_bn(self.conv3(inp)), 0.2)
        inp = F.leaky_relu(self.conv4_bn(self.conv4(inp)), 0.2)
        inp = F.leaky_relu(self.conv5_bn(self.conv5(inp)), 0.2)

        inp = torch.sigmoid(self.conv7(inp))

        return inp

 
def denorm(x):
    out = (x + 1) / 2  
    return out.clamp(0, 1)


# Save model
def save_model(epoch, path, nets, optimizers, net_name):
    netG, netD = nets
    optimizer_G, optimizer_D = optimizers
    print ("Saving model ------------------------------->")
    if not os.path.exists(os.path.join(path, net_name)):
        os.mkdir(os.path.join(path, net_name))
    torch.save({'epoch': epoch, 'state_dict': netG.state_dict(), 'optimizer' : optimizer_G.state_dict(), },
                f='%s/%s/%s_%d.pkl' % (path, net_name, 'G', epoch))
    torch.save({'epoch': epoch, 'state_dict': netD.state_dict(), 'optimizer' : optimizer_D.state_dict(), },
                f='%s/%s/%s_%d.pkl' % (path, net_name, 'D', epoch))
    print ("Finished ----------------------------------->")

def save_model_G(epoch, path, nets, optimizers, net_name, step):
    netG, netD = nets
    optimizer_G, optimizer_D = optimizers
    print ("Saving model ------------------------------->")
    if not os.path.exists(os.path.join(path, net_name)):
        os.mkdir(os.path.join(path, net_name))
    torch.save({'epoch': epoch, 'state_dict': netG.state_dict(), 'optimizer' : optimizer_G.state_dict(), },
                f='%s/%s/%s_%d_%d.pkl' % (path, net_name, 'G', epoch, step))
    print ("Finished ----------------------------------->")

# Save images
def save_images(net_name, epoch, PATH, src_img, pose, tgt_img, fake_img, summary):
    n, c, h, w = src_img.size()
    samples = torch.FloatTensor(4*n, c, h, w).zero_()
    for i in range(n):
        samples[4*i+0] = src_img[i].data
        samples[4*i+1] = pose[i].data
        samples[4*i+2] = tgt_img[i]
        samples[4*i+3] = fake_img[i].data

    images = utils.make_grid(samples, nrow=8, padding=30, normalize=True)
    summary.add_image('samples', images, epoch)
    file_name = os.path.join(PATH, net_name)
    if not os.path.exists(file_name):
        os.mkdir(file_name)
    utils.save_image(samples, '%s/samples_%d.png' % (file_name, epoch), nrow=8, padding=30, normalize=True)


# Load Data
def load_data():
    train_data = dataset.Market_DataLoader(imgs_path=cfg.TRAIN.imgs_path, pose_path=cfg.TRAIN.pose_path, idx_path=cfg.TRAIN.idx_path,
                                           transform=dataset.train_transform(), loader=dataset.val_loader)
    train_loader = Data.DataLoader(train_data, batch_size=cfg.TRAIN.BATCH_SIZE, shuffle=True,
                                   num_workers=cfg.TRAIN.NUM_WORKERS, drop_last=True)

    val_data = dataset.Market_DataLoader(imgs_path=cfg.TRAIN.imgs_path, pose_path=cfg.TRAIN.pose_path, idx_path=cfg.TEST.idx_path,
                                         transform=dataset.val_transform(), loader=dataset.val_loader)
    val_loader = Data.DataLoader(val_data, batch_size=cfg.TEST.BATCH_SIZE, shuffle=False,
                                 num_workers=cfg.TRAIN.NUM_WORKERS)

    train = [train_data, train_loader]
    val = [val_data, val_loader]
    return train, val


# Load Network
def load_network():

    print ('###################################')
    print ("#####      Build Network      #####")
    print ('###################################')

    nets = []
    #################################
    netG = UNet(6,3)
    netG = nn.DataParallel(netG)

    netD = Patch_Discriminator(64)
    netD = nn.DataParallel(netD)
    #################################

    nets.append(netG)
    nets.append(netD)

    for net in nets:
        net.cuda()

    return nets

def print_networks(model_names, debug):
        print ('---------------- Network initialized ----------------')
        names = ['netG', 'netD']
        for i, net in enumerate(model_names):
            num_params = 0
            for param in net.parameters():
                num_params += param.numel()
            if debug:
                print ('=========== %s ===========' % names[i])
                print (net)
            print ('[Network %s] Total number of parameters: %.3f M' % (names[i], num_params / 1e6))
        print ('-----------------------------------------------------')


# define optimizers
def Optimizer(nets):
    netG, netD = nets

    optimizer_G = torch.optim.Adam(netG.parameters(), lr=cfg.TRAIN.LR, betas=(0.5, 0.999))
    optimizer_D = torch.optim.Adam(netD.parameters(), lr=cfg.TRAIN.LR, betas=(0.5, 0.999))
    optimizers = [optimizer_G, optimizer_D]

    lr_policy = lambda epoch: (1 - 1 * max(0, epoch-cfg.TRAIN.LR_DECAY) / cfg.TRAIN.LR_DECAY)
    scheduler_G = lr_scheduler.LambdaLR(optimizer_G, lr_lambda=lr_policy)
    scheduler_D = lr_scheduler.LambdaLR(optimizer_D, lr_lambda=lr_policy)
    schedulers = [scheduler_G, scheduler_D]

    summary = SummaryWriter(log_dir='%s/%s' % (os.path.join(cfg.FILE_PATH, 'log'), cfg.NET), comment='')

    return optimizers, schedulers, summary


def loss_func():
    criterionGAN = torch.nn.MSELoss().cuda()
    criterionIdt = torch.nn.L1Loss().cuda()
    criterion = [criterionGAN, criterionIdt]

    return criterion


def train(train_file, val_file, nets, optimizers, schedulers, summary, criterion):
    print ('\n###################################')
    print ("#####      Start Traning      #####")
    print ('###################################')

    train_data, train_loader = train_file
    val_data, val_loader = val_file
    netG, netD = nets
    optimizer_G, optimizer_D = optimizers
    scheduler_G, scheduler_D = schedulers
    criterionGAN, criterionIdt = criterion
    count = 0
    for epoch in range(1, cfg.TRAIN.MAX_EPOCH+1):
        scheduler_G.step()
        scheduler_D.step()
        for step, (src_img, tgt_img, pose) in enumerate(train_loader):
            begin = time.time()

            # #######################################################
            # (1) Data process
            # #######################################################

            src_img = Variable(src_img).cuda()      # N x 3 x H x W
            tgt_img = Variable(tgt_img).cuda()      # N x 3 x H x W
            pose = Variable(pose).cuda()            # N x 3 x H x W


            # #######################################################
            # (2) Generate images
            # #######################################################
            # fake_img = netG(src_img, pose)
            fake_img = netG(torch.cat([src_img, pose], 1))


            # #######################################################
            # (3) Update Generators
            # #######################################################
            D_fake_img = netD(fake_img)
            # print(src_img.size(), pose.size(), fake_img.size())

            # D_fake_img = netD( pose, fake_img)
            G_loss = criterionGAN(D_fake_img, torch.ones_like(D_fake_img))
            idt_loss = criterionIdt(fake_img, tgt_img) * cfg.TRAIN.lambda_idt

            loss_G = G_loss + idt_loss
            optimizer_G.zero_grad()
            loss_G.backward()
            optimizer_G.step()


            # #######################################################
            # (4) Update Discriminators
            # #######################################################
            D_fake_img = netD(  fake_img.detach())
            D_real_img = netD(  tgt_img.detach())

            D_fake_loss = criterionGAN(D_fake_img, torch.zeros_like(D_fake_img))
            D_real_loss = criterionGAN(D_real_img, torch.ones_like(D_real_img))

            loss_D = D_fake_loss + D_real_loss
            optimizer_D.zero_grad()
            loss_D.backward()
            optimizer_D.step()


            # #######################################################
            # (5) Update Log and Display loss info.
            # #######################################################
            count += 1
            summary.add_scalar('G_loss', G_loss.data[0], count)
            summary.add_scalar('Idt_loss', idt_loss.data[0], count)
            summary.add_scalar('netG_loss', loss_G.data[0], count)
            summary.add_scalar('netD_loss', loss_D.data[0], count)

            print ('Epoch: {}/{}  |  Step: {}/{}  |  lr: {:.6f}  |  G_loss: {:.6f}  |  D_loss: {:.6f}  |  Idt_loss: {:.6f}  |  Time: {:.3f}'
               .format(epoch, cfg.TRAIN.MAX_EPOCH, step+1, len(train_loader), optimizer_G.param_groups[0]['lr'],
                       loss_G.data[0], loss_D.data[0], idt_loss.data[0], time.time()-begin))

            MODEL_PATH = os.path.join(cfg.FILE_PATH, 'model')
            if(step % 2000 == 0 and step!=0):
            	save_model_G(epoch, MODEL_PATH, nets, optimizers, cfg.NET, step)


        # #######################################################
        # (7) Validation
        # #######################################################

        #Can add with torch.no_grad():
        netG.eval()
        PATH = os.path.join(cfg.FILE_PATH, 'images')
        for _, (src_img, tgt_img, pose) in enumerate(val_loader):
            src_img = Variable(src_img, volatile=True).cuda()
            pose = Variable(pose, volatile=True).cuda()

            # fake_img = netG(src_img, pose)
            fake_img = netG(torch.cat([src_img, pose], 1))
            

            save_images(cfg.NET, epoch, PATH, src_img, pose, tgt_img, fake_img, summary)
            break    # For better comparison, we only test one specfic batch images
        netG.train()


        # #######################################################
        # (7) Save models per epoch
        # #######################################################
        MODEL_PATH = os.path.join(cfg.FILE_PATH, 'model')
        save_model(epoch, MODEL_PATH, nets, optimizers, cfg.NET)

    summary.close()


def main():
    train_file, val_file = load_data()
    nets = load_network()
    optimizers, schedulers, summary = Optimizer(nets)
    criterion = loss_func()
    train(train_file, val_file, nets, optimizers, schedulers, summary, criterion)


if __name__ == '__main__':
    main()
