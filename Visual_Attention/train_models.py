import argparse
import sys
sys.path.insert(0, '/proj/digbose92/VQA/VisualQuestion_VQA/Visual_All')
import numpy as np 
import torch
import torchvision.transforms as transforms
import json
import _pickle as cPickle
from torch.utils.data import Dataset, DataLoader
import os
import utils
from PIL import Image
from dataset_vqa import Dictionary, VQAFeatureDataset
import glob
import matplotlib.pyplot as plt
import cv2
from models import *
from tqdm import tqdm
import time
import h5py
from model_combined import *
import torch.nn.functional as F
from vqa_dataset_attention import *
import torch.nn as nn
import random

def instance_bce_with_logits(logits, labels):
    assert logits.dim() == 2

    loss = F.binary_cross_entropy_with_logits(logits, labels)
    loss *= labels.size(1)
    return loss

def compute_score_with_logits(logits, labels):
    logits = torch.max(logits, 1)[1].data # argmax
    one_hots = torch.zeros(*labels.size()).cuda()
    one_hots.scatter_(1, logits.view(-1, 1), 1)
    scores = (one_hots * labels)
    return scores

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--eval', action='store_true', help='set this to evaluate.')
    parser.add_argument('--epochs', type=int, default=40)
    parser.add_argument('--num_hid', type=int, default=1024) # they used 1024
    parser.add_argument('--dropout', type=float, default=0.3)
    parser.add_argument('--dropout_L', type=float, default=0.1)
    parser.add_argument('--dropout_G', type=float, default=0.2)
    parser.add_argument('--dropout_W', type=float, default=0.4)
    parser.add_argument('--dropout_C', type=float, default=0.5)
    parser.add_argument('--activation', type=str, default='LeakyReLU', help='PReLU, ReLU, LeakyReLU, Tanh, Hardtanh, Sigmoid, RReLU, ELU, SELU')
    parser.add_argument('--norm', type=str, default='weight', help='weight, batch, layer, none')
    parser.add_argument('--model', type=str, default='A3x2')
    parser.add_argument('--output', type=str, default='saved_models/')
    parser.add_argument('--batch_size', type=int, default=128)
    parser.add_argument('--weight_decay', type=float, default=0)
    parser.add_argument('--optimizer', type=str, default='Adamax', help='Adam, Adamax, Adadelta, RMSprop')
    parser.add_argument('--initializer', type=str, default='kaiming_normal')
    parser.add_argument('--seed', type=int, default=9731, help='random seed')
    args = parser.parse_args()
    return args

if __name__ == '__main__':
    
    image_root_dir="/data/digbose92/VQA/COCO"
    dictionary=Dictionary.load_from_file('../Visual_All/data/dictionary.pkl')
    feats_data_path="/data/digbose92/VQA/COCO/train_hdf5_COCO/"
    data_root="/proj/digbose92/VQA/VisualQuestion_VQA/common_resources"
    npy_file="../../VisualQuestion_VQA/Visual_All/data/glove6b_init_300d.npy"
    seed = 0
    args = parse_args()
    #device_selection
    device=1
    torch.cuda.set_device(device)

    if args.seed == 0:
        seed = random.randint(1, 10000)
        random.seed(seed)
        torch.manual_seed(seed)
        torch.cuda.manual_seed(args.seed)
    else:
        seed = args.seed
        torch.manual_seed(args.seed)
        torch.cuda.manual_seed(args.seed)
    
    #train dataset
    train_dataset=Dataset_VQA(img_root_dir=image_root_dir,feats_data_path=feats_data_path,dictionary=dictionary,dataroot=data_root,arch_choice="resnet152",layer_option="pool")
    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True, num_workers=8)
    total_step=len(train_loader)

    #model related issues
    model = attention_baseline(train_dataset, num_hid=args.num_hid, dropout= args.dropout, norm=args.norm,\
                               activation=args.activation, drop_L=args.dropout_L, drop_G=args.dropout_G,\
                               drop_W=args.dropout_W, drop_C=args.dropout_C)

    model=model.to(device)

    if args.initializer == 'xavier_normal':
        model.apply(weights_init_xn)
    elif args.initializer == 'xavier_uniform':
        model.apply(weights_init_xu)
    elif args.initializer == 'kaiming_normal':
        model.apply(weights_init_kn)
    elif args.initializer == 'kaiming_uniform':
        model.apply(weights_init_ku)

    model.w_emb.init_embedding(npy_file)

    
    if args.optimizer == 'Adadelta':
        optim = torch.optim.Adadelta(model.parameters(), rho=0.95, eps=1e-6, weight_decay=args.weight_decay)
    elif args.optimizer == 'RMSprop':
        optim = torch.optim.RMSprop(model.parameters(), lr=0.01, alpha=0.99, eps=1e-08, weight_decay=args.weight_decay, momentum=0, centered=False)
    elif args.optimizer == 'Adam':
        optim = torch.optim.Adam(model.parameters(), lr=0.001, betas=(0.9, 0.999), eps=1e-08, weight_decay=args.weight_decay)
    else:
        optim = torch.optim.Adamax(model.parameters(), weight_decay=args.weight_decay)

    print('Starting training')
    for epoch in range(args.epochs):
        total_loss = 0
        train_score = 0
        t = time.time()
        correct = 0
        step=0
        for i, (feat, quest, label, target) in enumerate(train_loader):
            feat = feat.to(device)
            quest = quest.to(device)
            target = target.to(device) # true labels

            pred = model(feat, quest, target)
            loss = instance_bce_with_logits(pred, target)
            print(loss)
            loss.backward()
            nn.utils.clip_grad_norm(model.parameters(), 0.25)
            optim.step()
            optim.zero_grad()

            batch_score = compute_score_with_logits(pred, target.data).sum()
            total_loss += loss.item() * feat.size(0)
            train_score += batch_score
            if(step%10==0):
            #optimizer.zero_grad()
                print('Epoch [{}/{}], Step [{}/{}], Loss: {:.4f}'
                    .format(epoch, args.epochs, step, total_step, loss.item()))
            step=step+1

        total_loss /= len(train_loader.dataset)
        train_score = 100 * train_score / len(train_loader.dataset)


    

