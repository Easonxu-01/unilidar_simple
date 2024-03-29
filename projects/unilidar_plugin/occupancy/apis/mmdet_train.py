# ---------------------------------------------
# Copyright (c) OpenMMLab. All rights reserved.
# ---------------------------------------------
#  Modified by Xiaofeng Wang
# ---------------------------------------------
import random
import warnings
import sys
import numpy as np
import torch
import torch.distributed as dist
from mmcv.parallel import MMDataParallel, MMDistributedDataParallel
from mmcv.runner import (HOOKS, DistSamplerSeedHook, EpochBasedRunner,
                         Fp16OptimizerHook, OptimizerHook, build_optimizer,
                         build_runner, get_dist_info)
from mmcv.utils import build_from_cfg

from mmdet.core import EvalHook

from mmdet.datasets import (build_dataset,
                            replace_ImageToTensor)
from mmdet.utils import get_root_logger
import time
import os.path as osp
from projects.unilidar_plugin.datasets.builder import build_dataloader
from projects.unilidar_plugin.core.evaluation.eval_hooks import OccDistEvalHook, OccEvalHook
from projects.unilidar_plugin.datasets import custom_build_dataset, ConcatenatedDataset
from mmdet3d.datasets import build_dataset
sys.setrecursionlimit(1000000)

import os

os.environ['JOBLIB_TEMP_FOLDER'] = '/data1' 

# import os, pandarallel

#clean up the /tmp folder	
# if os.path.isdir("/tmp") : 
#     os.system('rm -R /tmp/*')

# os.environ['JOBLIB_TEMP_FOLDER'] = '/tmp'	
# pandarallel.initialize(nb_workers = int(os.cpu_count())-1, use_memory_fs = False , progress_bar=True,verbose=2 ) 

def custom_train_detector(model,
                   dataset,
                   cfg,
                   distributed=False,
                   validate=False,
                   timestamp=None,
                   meta=None):
    
    logger = get_root_logger(cfg.log_level)
    
    dataset = dataset if isinstance(dataset, (list, tuple)) else [dataset]
    
    
    
    data_loaders = [
        build_dataloader(
            ds,
            cfg.data.samples_per_gpu,
            cfg.data.workers_per_gpu,
            # cfg.gpus will be ignored if distributed
            len(cfg.gpu_ids),
            dist=distributed,
            seed=cfg.seed,
            shuffler_sampler=cfg.data.shuffler_sampler,  # dict(type='DistributedGroupSampler'),
            nonshuffler_sampler=cfg.data.nonshuffler_sampler,  # dict(type='DistributedSampler'),
        ) for ds in dataset
    ]

    # put model on gpus
    if distributed:
        find_unused_parameters = cfg.get('find_unused_parameters', False)
        model = MMDistributedDataParallel(
            model.cuda(),
            device_ids=[torch.cuda.current_device()],
            broadcast_buffers=False,
            find_unused_parameters=find_unused_parameters)
    else:
        model = MMDataParallel(
            model.cuda(cfg.gpu_ids[0]), device_ids=cfg.gpu_ids)
    
    if dist.get_rank() == 0:
        # Rank 0的进程负责初始化参数，其他进程将从这里同步参数
        pass  # 这里可以加载预训练参数等

    # 确保所有进程同步模型参数
    for param in model.parameters():
        dist.broadcast(param.data, src=0)
    
    # build runner
    optimizer = build_optimizer(model, cfg.optimizer)

    assert 'runner' in cfg
    runner = build_runner(
        cfg.runner,
        default_args=dict(
            model=model,
            optimizer=optimizer,
            work_dir=cfg.work_dir,
            logger=logger,
            meta=meta))

    # an ugly workaround to make .log and .log.json filenames the same
    runner.timestamp = timestamp

    # fp16 setting TODO
    fp16_cfg = cfg.get('fp16', None)
    if fp16_cfg is not None:
        optimizer_config = Fp16OptimizerHook(
            **cfg.optimizer_config, **fp16_cfg, distributed=distributed)
    elif distributed and 'type' not in cfg.optimizer_config:
        optimizer_config = OptimizerHook(**cfg.optimizer_config)
    else:
        optimizer_config = cfg.optimizer_config

    # register hooks
    runner.register_training_hooks(cfg.lr_config, optimizer_config,
                                   cfg.checkpoint_config, cfg.log_config,
                                   cfg.get('momentum_config', None))
    
    if distributed:
        if isinstance(runner, EpochBasedRunner):
            runner.register_hook(DistSamplerSeedHook())

    # register eval hooks
    if validate:
        # Support batch_size > 1 in validation
        val_samples_per_gpu = cfg.data.val.pop('samples_per_gpu', 1)
        if val_samples_per_gpu > 1:
            assert NotImplementedError()
            # Replace 'ImageToTensor' to 'DefaultFormatBundle'
            cfg.data.val.pipeline = replace_ImageToTensor(
                cfg.data.val.pipeline)
        val_dataset = custom_build_dataset(cfg.data.val, dict(test_mode=True))

        val_dataloader = build_dataloader(
            val_dataset,
            samples_per_gpu=val_samples_per_gpu,
            workers_per_gpu=cfg.data.workers_per_gpu,
            dist=distributed,
            shuffle=False,
            shuffler_sampler=cfg.data.shuffler_sampler,  # dict(type='DistributedGroupSampler'),
            nonshuffler_sampler=cfg.data.nonshuffler_sampler,  # dict(type='DistributedSampler'),
        )
        eval_cfg = cfg.get('evaluation', {})
        eval_cfg['by_epoch'] = cfg.runner['type'] != 'IterBasedRunner'
        eval_cfg['jsonfile_prefix'] = osp.join('val', cfg.work_dir, time.ctime().replace(' ','_').replace(':','_'))
        eval_hook = OccDistEvalHook if distributed else OccEvalHook
        runner.register_hook(eval_hook(val_dataloader, **eval_cfg))

    # user-defined hooks
    if cfg.get('custom_hooks', None):
        custom_hooks = cfg.custom_hooks
        assert isinstance(custom_hooks, list), \
            f'custom_hooks expect list type, but got {type(custom_hooks)}'
        for hook_cfg in cfg.custom_hooks:
            assert isinstance(hook_cfg, dict), \
                'Each item in custom_hooks expects dict type, but got ' \
                f'{type(hook_cfg)}'
            hook_cfg = hook_cfg.copy()
            priority = hook_cfg.pop('priority', 'NORMAL')
            # hook = build_from_cfg(hook_cfg, HOOKS) 
            # FIXME hardcode specifying dataloader as parameter 
            hook = build_from_cfg(hook_cfg, HOOKS, {'dataloader': val_dataloader}) 
            runner.register_hook(hook, priority=priority)

    if cfg.resume_from:
        runner.resume(cfg.resume_from)
    elif cfg.load_from:
        runner.load_checkpoint(cfg.load_from)
    runner.run(data_loaders, cfg.workflow)


def custom_train_multidb_detector(model,
                   dataset_1,
                   dataset_2,
                   cfg,
                   distributed=False,
                   validate=False,
                   timestamp=None,
                   meta=None):
    
    logger = get_root_logger(cfg.log_level)
    
    dataset_1 = dataset_1 if isinstance(dataset_1, (list, tuple)) else [dataset_1]
    dataset_2 = dataset_2 if isinstance(dataset_2, (list, tuple)) else [dataset_2]
    
    merged_dataset = ConcatenatedDataset(dataset_1, dataset_2)
    # merged_dataset = [dataset_1[0], dataset_2[0]]
    
    data_loaders = [
        build_dataloader(
            merged_dataset,
            cfg.data_merge.samples_per_gpu,
            cfg.data_merge.workers_per_gpu,
            # cfg.gpus will be ignored if distributed
            len(cfg.gpu_ids),
            dist=distributed,
            seed=cfg.seed,
            shuffler_sampler=cfg.data_merge.shuffler_sampler,  # dict(type='DistributedGroupSampler'),
            nonshuffler_sampler=cfg.data_merge.nonshuffler_sampler,  # dict(type='DistributedSampler'),
            drop_last=True,
        )
    ]
    

    # put model on gpus
    if distributed:
        find_unused_parameters = cfg.get('find_unused_parameters', False)
        model = MMDistributedDataParallel(
            model.cuda(),
            device_ids=[torch.cuda.current_device()],
            broadcast_buffers=False,
            find_unused_parameters=find_unused_parameters)
        # model._set_static_graph()
    else:
        model = MMDataParallel(
            model.cuda(cfg.gpu_ids[0]), device_ids=cfg.gpu_ids)


    # build runner
    optimizer = build_optimizer(model, cfg.optimizer)

    assert 'runner' in cfg
    runner = build_runner(
        cfg.runner,
        default_args=dict(
            model=model,
            optimizer=optimizer,
            work_dir=cfg.work_dir,
            logger=logger,
            meta=meta))

    # an ugly workaround to make .log and .log.json filenames the same
    runner.timestamp = timestamp

    # fp16 setting TODO
    fp16_cfg = cfg.get('fp16', None)
    if fp16_cfg is not None:
        optimizer_config = Fp16OptimizerHook(
            **cfg.optimizer_config, **fp16_cfg, distributed=distributed)
    elif distributed and 'type' not in cfg.optimizer_config:
        optimizer_config = OptimizerHook(**cfg.optimizer_config)
    else:
        optimizer_config = cfg.optimizer_config

    # register hooks
    runner.register_training_hooks(cfg.lr_config, optimizer_config,
                                   cfg.checkpoint_config, cfg.log_config,
                                   cfg.get('momentum_config', None))
    
    if distributed:
        if isinstance(runner, EpochBasedRunner):
            runner.register_hook(DistSamplerSeedHook())

    # register eval hooks
    if validate:
        # Support batch_size > 1 in validation
        val_samples_per_gpu = cfg.data_nu.val.pop('samples_per_gpu', 1)
        
        # if val_samples_per_gpu > 1:
        #     assert NotImplementedError()
        #     # Replace 'ImageToTensor' to 'DefaultFormatBundle'
        #     cfg.data_nu.val.pipeline = replace_ImageToTensor(
        #         cfg.data_nu.val.pipeline)
        #     cfg.data_sk.val.pipeline = replace_ImageToTensor(
        #         cfg.data_sk.val.pipeline)
        # val_dataset_1 = [build_dataset(cfg.data_nu.val, dict(test_mode=True))]
        # val_dataset_2 = [build_dataset(cfg.data_sk.val, dict(test_mode=True))]
        # val_dataset_1 = val_dataset_1 if isinstance(val_dataset_1, (list, tuple)) else [val_dataset_1]
        # val_dataset_2 = val_dataset_2 if isinstance(val_dataset_2, (list, tuple)) else [val_dataset_2]

        # val_dataset_merge = ConcatenatedDataset(val_dataset_1, val_dataset_2)

        # val_dataloader = build_dataloader(
        #     val_dataset,
        #     samples_per_gpu=val_samples_per_gpu,
        #     workers_per_gpu=cfg.data_merge.workers_per_gpu,
        #     dist=distributed,
        #     shuffle=False,
        #     shuffler_sampler=cfg.data_merge.shuffler_sampler,  # dict(type='DistributedGroupSampler'),
        #     nonshuffler_sampler=cfg.data_merge.nonshuffler_sampler,  # dict(type='DistributedSampler'),
        #     drop_last=True,
        # )

        
        # eval_cfg_nu = cfg.get('evaluation_nu', {})
        # eval_cfg_nu['by_epoch'] = cfg.runner['type'] != 'IterBasedRunner'
        # eval_cfg_nu['jsonfile_prefix'] = osp.join('val', cfg.work_dir, time.ctime().replace(' ','_').replace(':','_'))
        # eval_cfg_sk = cfg.get('evaluation_sk', {})
        # eval_cfg_sk['by_epoch'] = cfg.runner['type'] != 'IterBasedRunner'
        # eval_cfg_sk['jsonfile_prefix'] = osp.join('val', cfg.work_dir, time.ctime().replace(' ','_').replace(':','_'))
        # eval_hook = OccDistEvalHook if distributed else OccEvalHook
        # runner.register_hook(eval_hook(val_dataloader, **eval_cfg_nu))
        # runner.register_hook(eval_hook(val_dataloader, **eval_cfg_sk))
        if val_samples_per_gpu > 1:
            assert NotImplementedError()
            # Replace 'ImageToTensor' to 'DefaultFormatBundle'
            cfg.data.val.pipeline = replace_ImageToTensor(
                cfg.data.val.pipeline)
        val_dataset = custom_build_dataset(cfg.data.val, dict(test_mode=True))

        val_dataloader = build_dataloader(
            val_dataset,
            samples_per_gpu=val_samples_per_gpu,
            workers_per_gpu=cfg.data_merge.workers_per_gpu,
            dist=distributed,
            shuffle=False,
            shuffler_sampler=cfg.data_merge.shuffler_sampler,  # dict(type='DistributedGroupSampler'),
            nonshuffler_sampler=cfg.data_merge.nonshuffler_sampler,  # dict(type='DistributedSampler'),
            drop_last=True,
        )
        eval_cfg = cfg.get('evaluation', {})
        eval_cfg['by_epoch'] = cfg.runner['type'] != 'IterBasedRunner'
        eval_cfg['jsonfile_prefix'] = osp.join('val', cfg.work_dir, time.ctime().replace(' ','_').replace(':','_'))
        eval_hook = OccDistEvalHook if distributed else OccEvalHook
        runner.register_hook(eval_hook(val_dataloader, **eval_cfg))

    # user-defined hooks
    if cfg.get('custom_hooks', None):
        custom_hooks = cfg.custom_hooks
        assert isinstance(custom_hooks, list), \
            f'custom_hooks expect list type, but got {type(custom_hooks)}'
        for hook_cfg in cfg.custom_hooks:
            assert isinstance(hook_cfg, dict), \
                'Each item in custom_hooks expects dict type, but got ' \
                f'{type(hook_cfg)}'
            hook_cfg = hook_cfg.copy()
            priority = hook_cfg.pop('priority', 'NORMAL')
            # hook = build_from_cfg(hook_cfg, HOOKS) 
            # FIXME hardcode specifying dataloader as parameter 
            hook = build_from_cfg(hook_cfg, HOOKS, {'dataloader': val_dataloader}) 
            runner.register_hook(hook, priority=priority)

    if cfg.resume_from:
        runner.resume(cfg.resume_from)
    elif cfg.load_from:
        runner.load_checkpoint(cfg.load_from)
    runner.run(data_loaders, cfg.workflow)
