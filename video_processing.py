""" Read and preprocess video data.
    Video processing occurs on a single video at a time. Video are read and
    preprocessed in parallel across multiple threads. The resulting videos
    are concatenated together to form a single batch for training or 
    evaluation.

    -- Provide processed video data for a network:
    inputs: Construct batches of evaluation examples of videos.
    distorted_inputs: Construct batches of training examples of videos.
    batch_inputs: Construct batches of training or evaluation examples of 
        videos.

    -- Data processing:
    parse_example_proto: Parses an Example proto containing a training example
        of a video.

    -- Image decoding:
    decode_jpeg: Decode a JPEG encoded string into a 3-D float32 Tensor.

    -- Video preprocessing:
    video_preprocessing: Decode and preprocess one video for evaluation or 
        training
    pre_video: Prepare one video.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import tensorflow as tf
import settings

FLAGS = tf.app.flags.FLAGS

def inputs(dataset, batch_size=None, num_preprocess_threads=None):
    """ Generate batches of videos for evaluation.

    Use this function as the inputs for evaluating a network.

    Note that some (minimal) video preprocessing occurs during evaluation
    including central cropping and resizing of the video to fit the network.

    Args:
        dataset: instance of Dataset class specifying the dataset.
        batch_size: integer, number of examples in batch, default value is 10
        num_preprocess_threads: integer, total number of preprocessing threads
            defaults to FLAGS.num_preprocess_threads.

    Returns:
        videos: Videos. 5D tensor of size [batch_size, sequence_size,
                                           row, column, 3].
        labels: 1-D integer Tensor of [FLAGS.batch_size].
        filenames: 1-D integer Tensor of [FLAGS.batch_size].
    """
    if not batch_size:
        batch_size = FLAGS.batch_size

    # Force all input processing onto CPU in order to reserve the GPU for
    # the forward inference and back-propagation.
    with tf.device('/cpu:0'):
        videos, labels, filenames = batch_inputs(
            dataset, batch_size, train=False,
            num_preprocess_threads=num_preprocess_threads)

    return videos, labels, filenames


def distorted_inputs(dataset, batch_size=None, num_preprocess_threads=None):
    """ Generate batches of distorted versions of videos.

    Use this function as the inputs for training a network.

    Distorting videos provides a useful technique for augmenting the data
    set during training in order to make the network invariant to aspects
    of the video that do not effect the label.

    Args:
        dataset: instance of Dataset class specifying the dataset.
        batch_size: integer, number of examples in batch
        num_preprocess_threads: integer, total number of preprocessing threads
            defaults to FLAGS.num_preprocess_threads.

    Returns:
        videos: Videos. 5-D tensor of size [batch_size, sequence_size,
                                            row, column, 3].
        labels: 1-D integer one host Tensor of [batch_size].
        filenames: 1-D integer Tensor of [batch_size].
    """
    if not batch_size:
        batch_size = FLAGS.batch_size

    # Force all input processing onto CPU in order to reserve the GPU for
    # the forward inference and back-propagation.
    videos, labels_one_hot, filenames = batch_inputs(
        dataset, batch_size, train=True,
        num_preprocess_threads=num_preprocess_threads)
    return videos, labels_one_hot, filenames


def decode_jpeg(image_buffer, scope=None):
    """ Decode a JPEG string into one 3-D float image Tensor.

    Args:
        image_buffer: scalar string Tensor.
        scope: Optional scope for op_scope.
    Returns:
        image: 3-D float Tensor with values ranging from [0, 1).
    """
    with tf.op_scope([image_buffer], scope, 'decode_jpeg'):
        # Decode the string as an RGB JPEG.
        # Note that the resulting image contains an unknown height and width
        # that is set dynamically by decode_jpeg. In other words, the height
        # and width of image is unknown at compile-time.
        image = tf.image.decode_jpeg(image_buffer, channels=3)

        # After this point, all image pixels reside in [0,1)
        # until the very end, when they're rescaled to (-1, 1).  The various
        # adjust_* ops all require this range for dtype float.
        image = tf.image.convert_image_dtype(image, dtype=tf.float32)
        return image


def pre_image(image, height, width, scope=None):
    """ Prepare one image.

    Args:
        image: 3-D float Tensor
        height: integer
        width: integer
        scope: Optional scope for op_scope.
    Returns:
        image: 3-D float Tensor of prepared image.
    """
    with tf.op_scope([image, height, width], scope, 'pre_image'):
        # Crop the central region of the image with an area containing 87.5% of
        # the original image.
        image = tf.image.central_crop(image, central_fraction=0.875)

        # Resize the image to the original height and width.
        image = tf.expand_dims(image, 0)
        image = tf.image.resize_bilinear(image, [height, width],
                                         align_corners=False)
        image = tf.squeeze(image, [0])
        return image


def video_preprocessing(images_features):
    """ Decode and preprocess one video for evaluation or training.

    Args:
        images_features: dictionary contains, Tensor tf.string containing the 
            contents of all images of a video.

    Returns:
        resutl: 4-D float Tensor containing an appropriately list of scaled 
            image [sequence_length, height, width, image_channel]
    """

    # convert the images_features dictionary to decoded images array
    images = []
    tmp_dict = {}
    for key, value in images_features.items(): 
        tmp_dict[int(key[-3:])] = images_features[key]
    images_features.clear()
    for index in range(len(tmp_dict)):
        images.append(decode_jpeg(tmp_dict[index]))

    height = FLAGS.image_height
    width = FLAGS.image_width

    for idx, image in enumerate(images):
        image = pre_image(image, height, width)

        # Finally, rescale to [-1,1] instead of [0, 1)
        images[idx] = tf.sub(image, 0.5)
        images[idx] = tf.mul(image, 2.0)

    # transfer the images list into a tensor
    for idx, image in enumerate(images):
        images[idx] = tf.expand_dims(image, 0)
    result = tf.concat(0, images)

    return result


def parse_example_proto(example_serialized):
    """ Parses an Example proto containing a training example of a video clip.

    The output of the convert_to_records.py video preprocessing script is a 
    dataset containing serialized Example protocol buffers. Each Example proto 
    contains the following fields:

        image/height: 200
        image/width: 100
        image/colorspace: 'RGB'
        image/channels: 3
        image/class/label: 2
        image/class/text: 'walking'
        image/format: 'JPEG'
        image/filename: '00001.JPEG'
        raw/image/001: <JPEG encoded string>
        ...
        raw/image/n: <JPEG encoded string>

    Args:
        example_serialized: scalar Tensor tf.string containing a serialized
        Example protocol buffer.

    Returns:
        image_features: dictionary contains, Tensor tf.string containing the 
        contents of all the JPEG file of a video.
        label: Tensor tf.int32 containing the label.
        text: Tensor tf.string containing the human-readable label.
        filename: the filename of the image
    """
    # Dense features in Example proto.
    feature_map = {
        'image/class/label': tf.FixedLenFeature([1], dtype=tf.int64,
                                                default_value=-1),
        'image/class/text': tf.FixedLenFeature([], dtype=tf.string,
                                                default_value=''),
        'image/filename': tf.FixedLenFeature([], dtype=tf.string,
                                            default_value='')
    }
    features = tf.parse_single_example(example_serialized, feature_map)
    label = tf.cast(features['image/class/label'], dtype=tf.int32)
    # subtract the label value by 1, becuae the previous label value range
    #  from(1..n)
    label = tf.sub(label, tf.constant(1))

    # images data in the Example proto
    image_map = {}
    for index in range(FLAGS.sequence_size):
        image_map['raw/image/%03d' % index] = tf.FixedLenFeature(
        [], 
        dtype=tf.string,
        default_value='')
    image_features = tf.parse_single_example(example_serialized, image_map) 

    return (image_features,
            label,
            features['image/class/text'],
            features['image/filename'])

def batch_inputs(dataset, batch_size, train, num_preprocess_threads=None):
    """ Contruct batches of training or evaluation examples from the video 
        dataset.

    Args:
        dataset: instance of Dataset class specifying the dataset. See 
            dataset.py for details.
        batch_size: integer
        train: boolean, shuffle the dataset or not
        num_preprocess_threads: integer, total number of preprocessing threads

    Returns:
        videos: 5-D float Tensor of a batch of videos
        labels: 1-D integer Tensor of [batch_size].
        filenames: an array contains all the filenames

    Raises:
        ValueError: if data is not found
    """
    with tf.name_scope('batch_processing'):
        data_files = dataset.data_files()
        if data_files is None:
        raise ValueError('No data files found for this dataset')

        # Create filename_queue
        if train:
        filename_queue = tf.train.string_input_producer(data_files,
                                                        shuffle=True,
                                                        capacity=16)
        else:
        filename_queue = tf.train.string_input_producer(data_files,
                                                        shuffle=False,
                                                        capacity=1)
        if num_preprocess_threads is None:
        num_preprocess_threads = FLAGS.num_preprocess_threads

        if num_preprocess_threads % 4:
        raise ValueError('Please make num_preprocess_threads a multiple '
                        'of 4 (%d % 4 != 0).', num_preprocess_threads)

        # Approximate number of examples per shard.
        examples_per_shard = 65
        min_queue_examples = examples_per_shard * FLAGS.input_queue_memory_factor
        if train:
            examples_queue = tf.RandomShuffleQueue(
                capacity=min_queue_examples + 3 * batch_size,
                min_after_dequeue=min_queue_examples,
                dtypes=[tf.string])
        else:
            examples_queue = tf.FIFOQueue(
                capacity=examples_per_shard + 3 * batch_size,
                dtypes=[tf.string])

        reader = dataset.reader()
        _, example_serialized = reader.read(filename_queue)

        videos_and_labels_and_filenames = []
        # Parse a serialized Example proto to extract the video and metadata.
        image_features, label_index, _, filename = parse_example_proto(
            example_serialized)
        video = video_preprocessing(image_features)
        videos_and_labels_and_filenames.append([video, label_index, filename])

        videos, label_index_batch, filename_batch = tf.train.batch_join(
            videos_and_labels_and_filenames,
            batch_size=batch_size,
            capacity=2 * num_preprocess_threads * batch_size)

        # Reshape images into these desired dimensions.
        height = FLAGS.image_height
        width = FLAGS.image_width
        depth = 3

        videos = tf.cast(videos, tf.float32)
        videos = tf.reshape(videos, shape=[batch_size, FLAGS.sequence_size, 
                                            height, width, depth])

        # Display the sample training images in the visualizer.
        images, *_ = tf.split(1, FLAGS.sequence_size, videos)
        images = tf.squeeze(images)
        tf.image_summary('video_first_image', images, max_images=batch_size)

        # convert the label to one hot vector
        labels = tf.reshape(label_index_batch, [batch_size])
        labels_one_hot = tf.one_hot(labels, dataset.num_classes(), 1, 0)

        return (videos, 
                labels_one_hot,
                tf.reshape(filename_batch, [batch_size]))
