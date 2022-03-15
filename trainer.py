import argparse
from ast import arg
import copy
import os
from posixpath import split
import shutil
import time

import pandas as pd
import torch
import torch.nn as nn
import torch.nn.parallel
import torch.backends.cudnn as cudnn
import torch.optim
import torch.utils.data
import torchvision.transforms as transforms
import torchvision.datasets as datasets
import resnet
import copy

model_names = sorted(name for name in resnet.__dict__
    if name.islower() and not name.startswith("__")
                     and name.startswith("resnet")
                     and callable(resnet.__dict__[name]))

print(model_names)

parser = argparse.ArgumentParser(description='Propert ResNets for CIFAR10 in pytorch')
parser.add_argument('--arch', '-a', metavar='ARCH', default='resnet32',
                    choices=model_names,
                    help='model architecture: ' + ' | '.join(model_names) +
                    ' (default: resnet32)')
parser.add_argument('-j', '--workers', default=4, type=int, metavar='N',
                    help='number of data loading workers (default: 4)')
parser.add_argument('--epochs', default=200, type=int, metavar='N',
                    help='number of total epochs to run')
parser.add_argument('--start-epoch', default=0, type=int, metavar='N',
                    help='manual epoch number (useful on restarts)')
parser.add_argument('-b', '--batch-size', default=128, type=int,
                    metavar='N', help='mini-batch size (default: 128)')
parser.add_argument('--lr', '--learning-rate', default=0.1, type=float,
                    metavar='LR', help='initial learning rate')
parser.add_argument('--momentum', default=0.9, type=float, metavar='M',
                    help='momentum')
parser.add_argument('--weight-decay', '--wd', default=1e-4, type=float,
                    metavar='W', help='weight decay (default: 1e-4)')
parser.add_argument('--print-freq', '-p', default=50, type=int,
                    metavar='N', help='print frequency (default: 50)')
parser.add_argument('--resume', default='', type=str, metavar='PATH',
                    help='path to latest checkpoint (default: none)')
parser.add_argument('-e', '--evaluate', dest='evaluate', action='store_true',
                    help='evaluate model on validation set')
parser.add_argument('--pretrained', dest='pretrained', action='store_true',
                    help='use pre-trained model')
parser.add_argument('--half', dest='half', action='store_true',
                    help='use half-precision(16-bit) ')
parser.add_argument('--save-dir', dest='save_dir',
                    help='The directory used to save the trained models',
                    default='save_temp', type=str)
parser.add_argument('--save-every', dest='save_every',
                    help='Saves checkpoints at every specified number of epochs',
                    type=int, default=10)
parser.add_argument('--seed', dest='seed',
                    help='saves checkpoints at every specified number of epochs',
                    type=int, default=44)
parser.add_argument('--ratio', dest='ratio',
                    help='saves checkpoints at every specified number of epochs',
                    type=float, default=1.0)
best_prec1 = 0


def main():
    global args, best_prec1
    args = parser.parse_args()
    set_seed(args.seed)
    epoch_list, rand_train_acc_list, rand_test_acc_list, norm_train_acc_list, norm_test_acc_list = [], [], []
    # Check the save_dir exists or not
    if not os.path.exists(args.save_dir):
        os.makedirs(args.save_dir)

    # random network to augment the cifar output
    gen_net = torch.nn.DataParallel(resnet.__dict__[args.arch]())
    gen_net.load_state_dict(torch.load('save_resnet20/randomizer.th')['state_dict'])
    gen_net.cuda()
    gen_net.eval()

    # models I will train
    norm_model = torch.nn.DataParallel(resnet.__dict__[args.arch]())
    rand_model = copy.deepcopy(norm_model)
    norm_model.cuda()
    rand_model.cuda()

    # optionally resume from a checkpoint
    # if args.resume:
    #     if os.path.isfile(args.resume):
    #         print("=> loading checkpoint '{}'".format(args.resume))
    #         checkpoint = torch.load(args.resume)
    #         args.start_epoch = checkpoint['epoch']
    #         best_prec1 = checkpoint['best_prec1']
    #         model.load_state_dict(checkpoint['state_dict'])
    #         print("=> loaded checkpoint '{}' (epoch {})"
    #               .format(args.evaluate, checkpoint['epoch']))
    #     else:
    #         print("=> no checkpoint found at '{}'".format(args.resume))

    cudnn.benchmark = True

    normalize = transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                     std=[0.229, 0.224, 0.225])

    # train_loader = torch.utils.data.DataLoader(
    #     datasets.CIFAR10(root='./data', train=True, transform=transforms.Compose([
    #         transforms.RandomHorizontalFlip(),
    #         transforms.RandomCrop(32, 4),
    #         transforms.ToTensor(),
    #         normalize,
    #     ]), download=True),
    #     batch_size=args.batch_size, shuffle=True,
    #     num_workers=args.workers, pin_memory=True)

    train_set_full = datasets.CIFAR10(root='./data', train=True, transform=transforms.Compose([
            # transforms.RandomHorizontalFlip(),
            # transforms.RandomCrop(32, 4),
            # transforms.ToTensor(),
            normalize,
        ]), download=True)
    
    # create sets - n/8 * len(data) for n(1,8)

    split = list(range(0, int(len(train_set_full)*args.ratio)))

    trainset = torch.utils.data.Subset(train_set_full, split)

    train_loader = torch.utils.data.DataLoader(trainset, batch_size=128,
                                                shuffle=True, num_workers=4)
                                                                                          

    val_loader = torch.utils.data.DataLoader(
        datasets.CIFAR10(root='./data', train=False, transform=transforms.Compose([
            transforms.ToTensor(),
            normalize,
        ])),
        batch_size=128, shuffle=False,
        num_workers=args.workers, pin_memory=True)

    # define loss function (criterion) and optimizer
    criterion = nn.CrossEntropyLoss().cuda()

    # if args.half:
    #     model.half()
    #     criterion.half()

    norm_optimizer = torch.optim.SGD(norm_model.parameters(), args.lr,
                                momentum=args.momentum,
                                weight_decay=args.weight_decay)

    norm_lr_scheduler = torch.optim.lr_scheduler.MultiStepLR(norm_optimizer,
                                                        milestones=[100, 150], last_epoch=args.start_epoch - 1)

    rand_optimizer = torch.optim.SGD(rand_model.parameters(), args.lr,
                                momentum=args.momentum,
                                weight_decay=args.weight_decay)

    rand_lr_scheduler = torch.optim.lr_scheduler.MultiStepLR(rand_optimizer,
                                                        milestones=[100, 150], last_epoch=args.start_epoch - 1)

    # if args.arch in ['resnet1202', 'resnet110']:
    #     # for resnet1202 original paper uses lr=0.01 for first 400 minibatches for warm-up
    #     # then switch back. In this setup it will correspond for first epoch.
    #     for param_group in optimizer.param_groups:
    #         param_group['lr'] = args.lr*0.1


    # if args.evaluate:
    #     validate(val_loader, model, criterion)
    #     return

    for epoch in range(args.start_epoch, args.epochs):

        # train for one epoch
        print('current lr {:.5e}'.format(norm_optimizer.param_groups[0]['lr']))
        train_rand, train_norm = train(train_loader, gen_net, rand_model, norm_model, criterion, rand_optimizer, norm_optimizer, epoch)
        norm_lr_scheduler.step()
        rand_lr_scheduler.step()

        # evaluate on validation set
        prec_rand, prec_norm = validate(val_loader, gen_net, rand_model, norm_model, criterion)
        

        # remember best prec@1 and save checkpoint
        #is_best = prec1 > best_prec1
        #best_prec1 = max(prec1, best_prec1)


        # save_checkpoint({
        #     'state_dict': model.state_dict(),
        #     'best_prec1': best_prec1,
        # }, is_best, filename=os.path.join(args.save_dir, 'model.th'))
        
        # write the data to and save it 
        print("Epoch: {} Random Test Accuracy: {} Normal Test Accuracy: {}".format(epoch, prec_rand, prec_norm))
        epoch_list.append(epoch)
        rand_train_acc_list.append(train_rand)
        rand_test_acc_list.append(prec_rand)
        norm_train_acc_list.append(train_norm)
        norm_test_acc_list.append(prec_norm)
        results_df = pd.DataFrame({'epoch': epoch_list, 'rand_train_acc': rand_train_acc_list, 'rand_test_acc': rand_test_acc_list,
                                    'norm_train_acc': norm_train_acc_list, 'norm_test_acc': norm_test_acc_list})
        results_df.to_csv('{}_split_accuracy.csv'.format(args.ratio))


def train(train_loader, gen_net, rand_model, norm_model, criterion, rand_optimizer, norm_optimizer, epoch):
    """
        Run one train epoch
    """
    batch_time = AverageMeter()
    data_time = AverageMeter()
    losses_norm = AverageMeter()
    losses_rand = AverageMeter()
    top1_norm = AverageMeter()
    top1_rand = AverageMeter()

    # switch to train mode
    norm_model.train()
    rand_model.train()

    end = time.time()
    for i, (input, target) in enumerate(train_loader):

        # measure data loading time
        data_time.update(time.time() - end)

        target = target.cuda()
        input_var = input.cuda()

        rand_target = gen_net(input_var).cuda()

        if args.half:
            input_var = input_var.half()

        # compute output
        rand_output = rand_model(input_var)
        rand_loss = criterion(rand_output, rand_target)

        norm_output = norm_model(input_var)
        norm_loss = criterion(norm_output, target)

        # compute gradient and do SGD step
        rand_optimizer.zero_grad()
        rand_loss.backward()
        rand_optimizer.step()

        norm_optimizer.zero_grad()
        norm_loss.backward()
        norm_optimizer.step()

        #need to fix from here down - pretty much make the output look nice

        output_rand = norm_output.float()
        loss_rand = rand_loss.float()

        # measure accuracy and record loss
        prec1_rand = accuracy(output_rand.data, target)[0]
        losses_rand.update(loss_rand.item(), input.size(0))
        top1_rand.update(prec1_rand.item(), input.size(0))

        output_norm = norm_output.float()
        loss_norm = norm_loss.float()

        # measure accuracy and record loss
        prec1_norm = accuracy(output_norm.data, target)[0]
        losses_norm.update(loss_norm.item(), input.size(0))
        top1_norm.update(prec1_norm.item(), input.size(0))

        # measure elapsed time
        batch_time.update(time.time() - end)
        end = time.time()



        # if i % args.print_freq == 0:
        #     print('Epoch: [{0}][{1}/{2}]\t'
        #           'Time {batch_time.val:.3f} ({batch_time.avg:.3f})\t'
        #           'Data {data_time.val:.3f} ({data_time.avg:.3f})\t'
        #           'Loss {loss.val:.4f} ({loss.avg:.4f})\t'
        #           'Prec@1 {top1.val:.3f} ({top1.avg:.3f})'.format(
        #               epoch, i, len(train_loader), batch_time=batch_time,
        #               data_time=data_time, loss=losses, top1=top1))
    
    return top1_rand.avg, top1_norm.avg


def validate(val_loader, gen_net, rand_model, norm_model, criterion):
    """
    Run evaluation
    """
    batch_time = AverageMeter()
    losses = AverageMeter()
    top1_rand = AverageMeter()
    top1_norm = AverageMeter()

    # switch to evaluate mode
    rand_model.eval()
    norm_model.eval()

    end = time.time()
    with torch.no_grad():
        for i, (input, target) in enumerate(val_loader):
            target = target.cuda()
            input_var = input.cuda()
            target_var = target.cuda()
            rand_target = gen_net(input_var).cuda

            if args.half:
                input_var = input_var.half()

            # compute output
            rand_output = rand_model(input_var)
            rand_loss = criterion(rand_output, rand_target)

            norm_output = norm_model(input_var)
            norm_loss = criterion(norm_output, target_var)

            output = output.float()
            loss = loss.float()

            # measure accuracy and record loss
            prec_rand = accuracy(output.data, target)[0]
            prec_norm = accuracy(output.data, target)[0]
            losses.update(loss.item(), input.size(0))
            top1_rand.update(prec_rand.item(), input.size(0))
            top1_norm.update(prec_norm.item(), input.size(0))

            # measure elapsed time
            batch_time.update(time.time() - end)
            end = time.time()

            # if i % args.print_freq == 0:
            #     print('Test: [{0}/{1}]\t'
            #           'Time {batch_time.val:.3f} ({batch_time.avg:.3f})\t'
            #           'Loss {loss.val:.4f} ({loss.avg:.4f})\t'
            #           'Prec@1 {top1.val:.3f} ({top1.avg:.3f})'.format(
            #               i, len(val_loader), batch_time=batch_time, loss=losses,
            #               top1=top1))

    # print(' * Prec@1 {top1.avg:.3f}'
    #       .format(top1=top1))

    return top1_rand.avg, top1_norm.avg

def save_checkpoint(state, is_best, filename='checkpoint.pth.tar'):
    """
    Save the training model
    """
    torch.save(state, filename)

class AverageMeter(object):
    """Computes and stores the average and current value"""
    def __init__(self):
        self.reset()

    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count


def accuracy(output, target, topk=(1,)):
    """Computes the precision@k for the specified values of k"""
    maxk = max(topk)
    batch_size = target.size(0)

    _, pred = output.topk(maxk, 1, True, True)
    pred = pred.t()
    correct = pred.eq(target.view(1, -1).expand_as(pred))

    res = []
    for k in topk:
        correct_k = correct[:k].view(-1).float().sum(0)
        res.append(correct_k.mul_(100.0 / batch_size))
    return res

def set_seed(seed):
    #random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    #np.random.seed(seed)
    os.environ['PYTHONHASHSEED'] = str(seed)
    # making sure GPU runs are deterministic even if they are slower
    torch.backends.cudnn.deterministic = True
    # this causes the code to vary across runs. I don't want that for now.
    # torch.backends.cudnn.benchmark = True
    print("Seeded everything: {}".format(seed))

if __name__ == '__main__':
    main()
