import sys
sys.path.insert(0, '..\\dcl')

import argparse
import gym
import sac.logz as logz
import numpy as np
import os
import tensorflow as tf
import time

import sac.nn as nn
from sac.sac import SAC
import sac.utils as utils

from multiprocessing import Process

# import CustomHumanoid
from envs.custom_humanoid_env import CustomHumanoidEnv
from envs.custom_lunar_lander import LunarLanderContinuous
from envs.custom_lunar_lander import GLOBAL_PARAMS

lunar_paras = dict(GLOBAL_PARAMS)
for k, v in GLOBAL_PARAMS.items():
    if not (isinstance(v, float) or isinstance(v, int)):
        del lunar_paras[k]


def dict2str(data):
    out = ''
    for k, v in data.items():
        out += k + '{0:.2f}'.format(v) + '-'
    return out


def get_perturbed_paras(paras_dict, max_paras=3, min_fold=0.2, max_fold=5):
    num_paras = np.random.randint(1, max_paras + 1)
    key_paras = np.random.choice(list(paras_dict.keys()), num_paras, replace=False)
    return_dict = dict(paras_dict)
    changed_paras = dict.fromkeys(key_paras)
    for para in key_paras:
        scalar = np.random.rand() * (max_fold - min_fold) + min_fold
        return_dict[para] = scalar * paras_dict[para]
        changed_paras[para] = return_dict[para]
    return return_dict, changed_paras


def train_SAC(env_name, exp_name, seed, logdir,
              two_qf=False, reparam=False, nepochs=100, paras={}):
    alpha = {
        'Ant-v2': 0.1,
        'HalfCheetah-v2': 0.2,
        'Hopper-v2': 0.2,
        'Humanoid-v2': 0.05,
        'Walker2d-v2': 0.2,
        'Toddler': 0.05,
        'Adult': 0.05,
        'LunarLander': 0.1
    }.get(env_name, 0.2)

    algorithm_params = {
        'alpha': alpha,
        'batch_size': 256,
        'discount': 0.99,
        'learning_rate': 1e-3,
        'reparameterize': reparam,
        'tau': 0.01,
        'epoch_length': 1000,
        'n_epochs': nepochs, # 500
        'two_qf': two_qf,
    }
    sampler_params = {
        'max_episode_length': 1000,
        'prefill_steps': 1000,
    }
    replay_pool_params = {
        'max_size': 1e6,
    }

    value_function_params = {
        'hidden_layer_sizes': (128, 128),
    }

    q_function_params = {
        'hidden_layer_sizes': (128, 128),
    }

    policy_params = {
        'hidden_layer_sizes': (128, 128),
    }

    logz.configure_output_dir(logdir)
    params = {
        'exp_name': exp_name,
        'env_name': env_name,
        'algorithm_params': algorithm_params,
        'sampler_params': sampler_params,
        'replay_pool_params': replay_pool_params,
        'value_function_params': value_function_params,
        'q_function_params': q_function_params,
        'policy_params': policy_params
    }
    logz.save_params(params)

    if env_name == 'Toddler' or env_name == 'Adult':
        env = CustomHumanoidEnv(template=env_name)
    elif env_name == 'LunarLander':
        env = LunarLanderContinuous(**paras)
    else:
        env = gym.envs.make(env_name)

    # Observation and action sizes
    ac_dim = env.action_space.n \
        if isinstance(env.action_space, gym.spaces.Discrete) \
        else env.action_space.shape[0]

    # Set random seeds
    tf.set_random_seed(seed)
    np.random.seed(seed)
    env.seed(seed)

    q_function = nn.QFunction(name='q_function', **q_function_params)
    if algorithm_params.get('two_qf', False):
        q_function2 = nn.QFunction(name='q_function2', **q_function_params)
    else:
        q_function2 = None
    value_function = nn.ValueFunction(
        name='value_function', **value_function_params)
    target_value_function = nn.ValueFunction(
        name='target_value_function', **value_function_params)
    policy = nn.GaussianPolicy(
        action_dim=ac_dim,
        reparameterize=algorithm_params['reparameterize'],
        **policy_params)

    samplers = []
    replay_pools = []

    sampler = utils.SimpleSampler(**sampler_params)
    replay_pool = utils.SimpleReplayPool(
        observation_shape=env.observation_space.shape,
        action_shape=(ac_dim,),
        **replay_pool_params)
    sampler.initialize(env, policy, replay_pool)
    samplers.append(sampler)
    replay_pools.append(replay_pool)

    algorithm = SAC(**algorithm_params)

    tf_config = tf.ConfigProto(inter_op_parallelism_threads=1, intra_op_parallelism_threads=1)
    tf_config.gpu_options.allow_growth = True  # may need if using GPU
    with tf.Session(config=tf_config):
        algorithm.build(
            env=env,
            policy=policy,
            q_function=q_function,
            q_function2=q_function2,
            value_function=value_function,
            target_value_function=target_value_function)
        # algorithm_params.get('n_epochs', 1000)
        for epoch in algorithm.train(sampler, n_epochs=algorithm_params.get('n_epochs', 100)):
            logz.log_tabular('Iteration', epoch)
            for k, v in algorithm.get_statistics().items():
                logz.log_tabular(k, v)
            for k, v in replay_pool.get_statistics().items():
                logz.log_tabular(k, v)
            for k, v in sampler.get_statistics().items():
                logz.log_tabular(k, v)
            logz.dump_tabular()


def train_func(args, logdir, seed, mutated_paras):
    train_SAC(
        env_name=args.env_name,
        exp_name=args.exp_name,
        seed=seed,
        logdir=os.path.join(logdir, '%d' % seed),
        two_qf=args.two_qf,
        reparam=args.reparam,
        nepochs=args.n_epochs,
        paras=mutated_paras
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env_name', type=str, default='Toddler')
    parser.add_argument('--exp_name', type=str, default=None)
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--n_experiments', '-e', type=int, default=1)
    parser.add_argument('--two_qf', action='store_true')
    parser.add_argument('--reparam', action='store_true')
    parser.add_argument('--n_epochs', '-ep', type=int, default=100)
    args = parser.parse_args()

    data_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'data')

    if args.exp_name is None:
        while True:
            mutated_paras, changed_paras = get_perturbed_paras(lunar_paras)
            setattr(args, 'exp_name', dict2str(changed_paras))

            if not (os.path.exists(data_path)):
                os.makedirs(data_path)
            logdir = 'sac_' + args.env_name + '_' + args.exp_name + '_' + time.strftime("%d-%m-%Y_%H-%M-%S")
            logdir = os.path.join(data_path, logdir)

            processes = []

            for e in range(args.n_experiments):
                seed = args.seed + 10*e
                print('Running experiment with seed %d' % seed)

                """
                def train_func():
                    train_SAC(
                        env_name=args.env_name,
                        exp_name=args.exp_name,
                        seed=seed,
                        logdir=os.path.join(logdir, '%d' % seed),
                    )
                """
                # # Awkward hacky process runs, because Tensorflow does not like
                # # repeatedly calling train_AC in the same thread.
                p = Process(target=train_func, args=(args, logdir, seed, mutated_paras))
                p.start()
                processes.append(p)
                # if you comment in the line below, then the loop will block
                # until this process finishes
                # p.join()

            for p in processes:
                p.join()


if __name__ == '__main__':
    os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
    tf.logging.set_verbosity(tf.logging.FATAL)
    main()
