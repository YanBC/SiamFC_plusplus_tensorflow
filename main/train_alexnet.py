import tensorflow.compat.v1 as tf
from tensorflow.compat.v1.train import AdamOptimizer
import os
import signal
import numpy as np
import time
import argparse
import sys
sys.path.append('.')

from networks.models import AlexNet
from data.classification_datasets import Data, Imagenet2012, Alexnet_Formater, Alexnet_Sampler
from data.dataloader import DataLoader


def classification_loss(y_true, y_pred):
    loss = -1 * y_true * tf.log(y_pred)
    return tf.math.reduce_sum(loss) / tf.cast(y_true.shape[0], tf.float32)

def SIGINT_handler(signum, frame):
    print(f'Sigal #{signum} receive. Exiting...')
    global datagen
    datagen.shutdown()
    exit(0)

def log_to_file(filepath, message, endwith='\n'):
    try:
        with open(filepath, 'a+') as f:
            f.write(message+endwith)
        return True
    except Exception as e:
        print(e)
        return False


def create_datagen(input_size=224, input_channel=3, num_cls=1000, batchsize=16):
    imagenet_dir = './datasets/imagenet'
    dataName = 'imagenet2012_filtered.pkl'
    dataset = Imagenet2012(imagenet_dir)
    dataset.load_data_from_file(os.path.join(dataset.storage, dataName))
    channel_mean = dataset.channel_mean

    formater = Alexnet_Formater(input_size, channel_mean, num_cls)
    sampler = Alexnet_Sampler(dataset, formater, batchsize)
    datagen = DataLoader(sampler, num_worker=8, buffer_size=64)
    return datagen


def train_job(lr, total_steps, global_step, report_interval=100, log_file='./alexnet_train.log', ckpt_name='./ckpt'):
    global datagen
    global sess
    global saver
    global loss_t, op_minimize, img_t, y_true_t, lr_t

    last_interval = None
    losses = []
    log_to_file(log_file, f'Setting learning rate to be {lr} for the next {total_steps} steps...')

    for step in range(total_steps):
        data = datagen.load_one()
        loss, result = sess.run([loss_t, op_minimize], feed_dict={img_t: data['X'], y_true_t: data['Y'], lr_t: lr})
        losses.append(loss)
        global_step += 1

        if loss is np.nan:
            save_path = saver.save(sess, ckpt_name, global_step=global_step)
            log_to_file(log_file, f'Loss grows to nan\nSave model to {save_path}')
            return False, global_step

        if step % report_interval == 0 and len(losses) > 1:
            if last_interval is None:
                pass
            else:
                now = time.time()
                log_to_file(log_file, 'loss: %0.3f    time: %0.3f steps per second' % (np.array(losses).mean(), report_interval/(now-last_interval)))
                losses = []
            last_interval = time.time()

    save_path = saver.save(sess, ckpt_name, global_step=global_step)
    log_to_file(log_file, f'Save model to {save_path}')
    return True, global_step


def get_opts():
    p = argparse.ArgumentParser('Train AlexNet')
    p.add_argument('--ckpt', help='restore weights from the given checkpoint path')
    p.add_argument('--gpus', default='0', help='specify gpus')
    return p.parse_args()


if __name__ == '__main__':
    opts = get_opts()
    if opts.ckpt is not None:
        restore_path = opts.ckpt
    else:
        restore_path = None
    os.environ['CUDA_VISIBLE_DEVICES'] = opts.gpus

    input_size = 224
    input_channel = 3
    num_cls = 1000
    batchsize = 16
    record_dir = './alexnet_record'
    if not os.path.isdir(record_dir):
        os.mkdir(record_dir)
    ckpt_name = os.path.join(record_dir, 'ckpt')
    log_name = os.path.join(record_dir, 'alexnet_train.log')

    datagen = create_datagen(input_size, input_channel, num_cls, batchsize)
    signal.signal(signal.SIGINT, SIGINT_handler)

    graph = tf.Graph()
    with graph.as_default():
        with tf.name_scope('Input'):
            img_t = tf.placeholder(tf.float32, shape=(batchsize, input_size, input_size, input_channel), name='image')
            y_true_t = tf.placeholder(tf.float32, shape=(batchsize, num_cls), name='y_true')
        network = AlexNet()
        output_t = network(img_t)
        loss_t = classification_loss(y_true_t, output_t)

        saver = tf.train.Saver(var_list=network.trainable_variables)
       
        sess = tf.Session()
        if restore_path is not None:
            saver.restore(sess, restore_path)
            global_step = int(restore_path.split('-')[-1])
        else:
            init_op = tf.global_variables_initializer()
            sess.run(init_op)
            global_step = 0

        lr_t = tf.placeholder(tf.float32, name='learning_rate', shape=())
        optimizer = AdamOptimizer(learning_rate=lr_t)
        op_minimize = optimizer.minimize(loss_t)
        sess.run(tf.variables_initializer(optimizer.variables()))

        learning_rates = np.linspace(1e-7, 1e-4, 20)
        for i, lr in enumerate(learning_rates):
            success, global_step = train_job(lr=lr, total_steps=2000, global_step=global_step, report_interval=500, ckpt_name=ckpt_name, log_file=log_name)
            if not success:
                print('Loss becomes nan. Exiting...')
                break

        learning_rates = np.linspace(1e-4, 1e-6, 50)
        for i, lr in enumerate(learning_rates):
            success, global_step = train_job(lr=lr, total_steps=2000, global_step=global_step, report_interval=500, ckpt_name=ckpt_name, log_file=log_name)
            if not success:
                print('Loss becomes nan. Exiting...')
                break

    datagen.shutdown()


