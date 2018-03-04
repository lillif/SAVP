import numpy as np
import tensorflow as tf

from video_prediction.models import vgg_network


def _axis(keep_axis, ndims):
    if keep_axis is None:
        axis = None
    else:
        axis = list(range(ndims))
        try:
            for keep_axis_ in keep_axis:
                axis.remove(keep_axis_)
        except TypeError:
            axis.remove(keep_axis)
        axis = tuple(axis)
    return axis


def peak_signal_to_noise_ratio_np(true, pred, keep_axis=None):
    ndims = max(true.ndim, pred.ndim)
    mse = mean_squared_error_np(true, pred, keep_axis=list(range(ndims))[:-3])
    psnr = 10.0 * np.log(1.0 / mse) / np.log(10.0)
    return np.mean(psnr, axis=_axis(keep_axis, psnr.ndim))


def mean_squared_error_np(true, pred, keep_axis=None):
    error = true - pred
    return np.mean(np.square(error), axis=_axis(keep_axis, error.ndim))


def structural_similarity_np(true, pred, K1=0.01, K2=0.03, win_size=7,
                             data_range=1.0, use_sample_covariance=True,
                             keep_axis=None):
    from skimage.measure import compare_ssim
    kwargs = dict(K1=K1, K2=K2,
                  win_size=win_size,
                  data_range=data_range,
                  multichannel=True,
                  use_sample_covariance=use_sample_covariance)
    assert true.shape == pred.shape
    shape = true.shape
    true = true.reshape((-1,) + shape[-3:])
    pred = pred.reshape((-1,) + shape[-3:])
    ssim = []
    for true_y, pred_y in zip(true, pred):
        ssim.append(compare_ssim(true_y, pred_y, **kwargs))
    ssim = np.reshape(ssim, shape[:-3])
    return np.mean(ssim, axis=_axis(keep_axis, ssim.ndim))


def expected_pixel_distribution_np(pix_distrib):
    pix_distrib = pix_distrib / np.sum(pix_distrib, axis=(-3, -2), keepdims=True)
    height, width = pix_distrib.shape[-3:-1]
    xv, yv = np.meshgrid(np.arange(width), np.arange(height))
    return np.stack([np.sum(yv[:, :, None] * pix_distrib, axis=(-3, -2, -1)),
                     np.sum(xv[:, :, None] * pix_distrib, axis=(-3, -2, -1))], axis=-1)


def expected_pixel_distance_np(true_pix_distrib, pred_pix_distribs, keep_axis=None):
    error = expected_pixel_distribution_np(true_pix_distrib) - \
            expected_pixel_distribution_np(pred_pix_distribs)
    return np.linalg.norm(error, axis=_axis(keep_axis, error.ndim))


def peak_signal_to_noise_ratio(true, pred, keep_axis=None):
    """
    Image quality metric based on maximal signal power vs. power of the noise.

    Args:
        true: the ground truth image.
        pred: the predicted image.
        keep_axis: None or int or iterable of ints (all non-negative).

    Returns:
        peak signal to noise ratio (PSNR).
    """
    true = tf.convert_to_tensor(true)
    pred = tf.convert_to_tensor(pred)
    ndims = max(true.shape.ndims, pred.shape.ndims)
    mse = mean_squared_error(true, pred, keep_axis=list(range(ndims))[:-3])
    psnr = 10.0 * tf.log(1.0 / mse) / tf.cast(tf.log(10.0), mse.dtype)
    return tf.reduce_mean(psnr, axis=_axis(keep_axis, psnr.shape.ndims))


def mean_squared_error(true, pred, keep_axis=None):
    """
    L2 distance between tensors true and pred.

    Args:
        true: the ground truth image.
        pred: the predicted image.
        keep_axis: None or int or iterable of ints (all non-negative).

    Returns:
        mean squared error between ground truth and predicted image.
    """
    true = tf.convert_to_tensor(true)
    pred = tf.convert_to_tensor(pred)
    error = true - pred
    return tf.reduce_mean(tf.square(error), axis=_axis(keep_axis, error.shape.ndims))


def _with_flat_batch(flat_batch_fn):
    def fn(x, *args, **kwargs):
        shape = tf.shape(x)
        flat_batch_x = tf.reshape(x, tf.concat([[-1], shape[-3:]], axis=0))
        flat_batch_r = flat_batch_fn(flat_batch_x, *args, **kwargs)
        r = tf.reshape(flat_batch_r, tf.concat([shape[:-3], flat_batch_r.shape[1:]], axis=0))
        return r
    return fn


def structural_similarity(X, Y, K1=0.01, K2=0.03, win_size=7,
                          data_range=1.0, use_sample_covariance=True,
                          keep_axis=None):
    """
    Structural SIMilarity (SSIM) index between two images

    Args:
        X: A tensor of shape `[..., in_height, in_width, in_channels]`.
        Y: A tensor of shape `[..., in_height, in_width, in_channels]`.
        keep_axis: None or int or iterable of ints (all non-negative).

    Returns:
        The SSIM between images X and Y.

    Reference:
        https://github.com/scikit-image/scikit-image/blob/master/skimage/measure/_structural_similarity.py

    Broadcasting is supported.
    """
    X = tf.convert_to_tensor(X)
    Y = tf.convert_to_tensor(Y)

    ndim = 2  # number of spatial dimensions
    nch = tf.shape(X)[-1]

    filter_func = _with_flat_batch(tf.nn.depthwise_conv2d)
    kernel = tf.cast(tf.fill([win_size, win_size, nch, 1], 1 / win_size ** 2), X.dtype)
    filter_args = {'filter': kernel, 'strides': [1] * 4, 'padding': 'VALID'}

    NP = win_size ** ndim

    # filter has already normalized by NP
    if use_sample_covariance:
        cov_norm = NP / (NP - 1)  # sample covariance
    else:
        cov_norm = 1.0  # population covariance to match Wang et. al. 2004

    # compute means
    ux = filter_func(X, **filter_args)
    uy = filter_func(Y, **filter_args)

    # compute variances and covariances
    uxx = filter_func(X * X, **filter_args)
    uyy = filter_func(Y * Y, **filter_args)
    uxy = filter_func(X * Y, **filter_args)
    vx = cov_norm * (uxx - ux * ux)
    vy = cov_norm * (uyy - uy * uy)
    vxy = cov_norm * (uxy - ux * uy)

    R = data_range
    C1 = (K1 * R) ** 2
    C2 = (K2 * R) ** 2

    A1, A2, B1, B2 = ((2 * ux * uy + C1,
                       2 * vxy + C2,
                       ux ** 2 + uy ** 2 + C1,
                       vx + vy + C2))
    D = B1 * B2
    S = (A1 * A2) / D

    ssim = tf.reduce_mean(S, axis=[-3, -2, -1])
    return tf.reduce_mean(ssim, axis=_axis(keep_axis, ssim.shape.ndims))


def normalize_tensor(tensor, eps=1e-10):
    norm_factor = tf.norm(tensor, axis=-1, keep_dims=True)
    return tensor / (norm_factor + eps)


def cosine_similarity(tensor0, tensor1, keep_axis=None):
    tensor0 = normalize_tensor(tensor0)
    tensor1 = normalize_tensor(tensor1)
    csim = tf.reduce_sum(tensor0 * tensor1, axis=-1)
    return tf.reduce_mean(csim, axis=_axis(keep_axis, csim.shape.ndims))


def cosine_distance(tensor0, tensor1, keep_axis=None):
    """
    Equivalent to:
        tensor0 = normalize_tensor(tensor0)
        tensor1 = normalize_tensor(tensor1)
        return tf.reduce_mean(tf.reduce_sum(tf.square(tensor0 - tensor1), axis=-1)) / 2.0
    """
    return 1.0 - cosine_similarity(tensor0, tensor1, keep_axis=keep_axis)


def vgg_cosine_distance(image0, image1, keep_axis=None):
    def _vgg_cosine_distance(image0, image1):
        assert image0.shape.ndims == 4
        assert image1.shape.ndims == 4
        with tf.variable_scope('vgg', reuse=tf.AUTO_REUSE):
            _, features0 = vgg_network.vgg16(image0)
        with tf.variable_scope('vgg', reuse=tf.AUTO_REUSE):
            _, features1 = vgg_network.vgg16(image1)
        cdist = 0.0
        for feature0, feature1 in zip(features0, features1):
            cdist += cosine_distance(feature0, feature1, keep_axis=0)
        return cdist

    image0 = tf.convert_to_tensor(image0, dtype=tf.float32)
    image1 = tf.convert_to_tensor(image1, dtype=tf.float32)
    shape = image0.shape
    if shape.ndims == 3:
        cdist = tf.squeeze(_vgg_cosine_distance(
            tf.expand_dims(image0, 0), tf.expand_dims(image1, 0)), 0)
    elif shape.ndims == 4:
        cdist = _vgg_cosine_distance(image0, image1)
    elif shape.ndims > 4:
        image0 = tf.reshape(image0, tf.concat([[-1], shape[-4:]], axis=0))
        image1 = tf.reshape(image1, tf.concat([[-1], shape[-4:]], axis=0))
        cdist = tf.map_fn(lambda args: _vgg_cosine_distance(*args),
                          (image0, image1), dtype=image0.dtype)
        cdist = tf.reshape(cdist, shape[:-3])
    else:
        raise ValueError
    return tf.reduce_mean(cdist, axis=_axis(keep_axis, cdist.shape.ndims))


def normalize_tensor_np(tensor, eps=1e-10):
    norm_factor = np.linalg.norm(tensor, axis=-1, keep_dims=True)
    return tensor / (norm_factor + eps)


def cosine_similarity_np(tensor0, tensor1, keep_axis=None):
    tensor0 = normalize_tensor_np(tensor0)
    tensor1 = normalize_tensor_np(tensor1)
    csim = np.sum(tensor0 * tensor1, axis=-1)
    return np.mean(csim, axis=_axis(keep_axis, csim.ndim))


def cosine_distance_np(tensor0, tensor1, keep_axis=None):
    """
    Equivalent to:
        tensor0 = normalize_tensor_np(tensor0)
        tensor1 = normalize_tensor_np(tensor1)
        return np.mean(np.sum(np.square(tensor0 - tensor1), axis=-1)) / 2.0
    """
    return 1.0 - cosine_similarity_np(tensor0, tensor1, keep_axis=keep_axis)


def vgg_cosine_distance_np(image0, image1, keep_axis=None, sess=None):
    if sess is None:
        sess = tf.Session()
        cdist = vgg_cosine_distance(image0, image1, keep_axis=keep_axis)
        sess.run(tf.global_variables_initializer())
        vgg_network.vgg_assign_from_values_fn(var_name_prefix='vgg/')(sess)
    else:
        cdist = vgg_cosine_distance(image0, image1, keep_axis=keep_axis)
    cdist = sess.run(cdist)
    return cdist


def test_ssim():
    import numpy as np
    from skimage.measure import compare_ssim

    batch_size = 4
    image_shape = (64, 64, 3)
    true = np.random.random((batch_size,) + image_shape)
    pred = np.random.random((batch_size,) + image_shape)

    sess = tf.Session()
    ssim_tf = structural_similarity(true, pred)
    ssim_tf = sess.run(ssim_tf)
    ssim_np = structural_similarity_np(true, pred)
    ssim = np.mean([compare_ssim(true_y, pred_y, data_range=1.0, multichannel=True)
                    for true_y, pred_y in zip(true, pred)])
    print(ssim_tf, ssim_np, ssim)


def test_ssim_broadcasting():
    import numpy as np

    batch_size = 4
    image_shape = (64, 64, 3)
    true = np.random.random((batch_size,) + image_shape)
    pred = np.random.random((10, batch_size,) + image_shape)

    sess = tf.Session()
    ssim_tf = structural_similarity(true, pred)
    ssim_tf = sess.run(ssim_tf)
    ssim_np = np.mean([structural_similarity_np(true, pred_) for pred_ in pred])
    print(ssim_tf, ssim_np)


def test_metrics_equivalence():
    import numpy as np

    a = np.random.random((10, 16, 64, 64, 3))
    b = np.random.random((10, 16, 64, 64, 3))
    metrics = [mean_squared_error,
               peak_signal_to_noise_ratio,
               structural_similarity]
    metrics_np = [mean_squared_error_np,
                  peak_signal_to_noise_ratio_np,
                  structural_similarity_np]
    sess = tf.Session()
    with tf.variable_scope('vgg'):
        vgg_network.vgg16(tf.placeholder(tf.float32, shape=[None] * 4))
    sess.run(tf.global_variables_initializer())
    vgg_network.vgg_assign_from_values_fn(var_name_prefix='vgg/')(sess)

    for keep_axis in (None, 0, 1, (0, 1)):
        for metric, metric_np in zip(metrics, metrics_np):
            m = metric(a, b, keep_axis=keep_axis)
            m_np = metric_np(a, b, keep_axis=keep_axis)
            assert np.allclose(sess.run(m), m_np, atol=1e-7)

        m = vgg_cosine_distance(a, b, keep_axis=keep_axis)
        m_np = vgg_cosine_distance_np(a, b, keep_axis=keep_axis, sess=sess)
        assert np.allclose(sess.run(m), m_np)
    print('The test metrics returned the same values.')


if __name__ == '__main__':
    test_ssim()
    test_ssim_broadcasting()
    test_metrics_equivalence()
