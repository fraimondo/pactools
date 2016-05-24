from __future__ import print_function

import os

import numpy as np
from scipy import signal
import matplotlib.pyplot as plt

from .io.smr2mne import smr2sig
from .utils.spectrum import Spectrum
from .utils.carrier import Carrier
from .utils.dehumming import dehummer
from .utils.arma import Arma


def _decimate(x, q):
    """
    Downsample the signal by using a filter.
    An order 16 Chebyshev type I filter is used.

    Parameters
    ----------
    x : ndarray
        The signal to be downsampled, as an N-dimensional array.
    q : int
        The downsampling factor.
    Returns
    -------
    y : ndarray
        The down-sampled signal.
    """
    if not isinstance(q, int):
        raise(TypeError, "q must be an integer")

    b, a = signal.filter_design.cheby1(16, 0.025, 0.98 / q)

    y = signal.filtfilt(b, a, x, axis=-1)

    sl = [slice(None)] * y.ndim
    sl[-1] = slice(None, None, q)
    return y[sl]


def decimate(sig, fs, decimation_factor):
    """Decimates the signal

    sig               : raw input signal
    fs                : sampling frequency of raw input signal
    decimation_factor : ratio of sampling frequencies (old/new)

    returns the new signal and its sampling frequency

    """
    # -------- center the signal
    sig = sig - np.mean(sig)

    # -------- resample
    # decimation could be performed in two steps for better performance
    # 0 in the following array means no decimation
    dec_1st = [0, 0, 2, 3, 4, 5, 6, 7, 2, 3, 2, 0, 3, 0, 2, 3, 4, 0, 3, 0, 4,
               3, 0, 0, 4, 5, 0, 3, 4, 0, 5]
    dec_2nd = [0, 0, 0, 0, 0, 0, 0, 0, 4, 3, 5, 0, 4, 0, 7, 5, 4, 0, 6, 0, 5,
               7, 0, 0, 6, 5, 0, 9, 7, 0, 6]

    d1 = dec_1st[decimation_factor]
    if d1 == 0:
        raise(ValueError, 'cannot decimate by %d' % decimation_factor)

    sig = _decimate(sig, d1)
    sig = sig.astype(np.float32)
    d2 = dec_2nd[decimation_factor]
    if d2 > 0:
        sig = _decimate(sig, d2)
        sig = sig.astype(np.float32)

    fs = fs / decimation_factor

    # -------- return decimated signal
    return sig, fs


def extract_and_fill(sig, fs, fc, n_cycles=None, bandwidth=1.0, fill=0,
                     draw='', ordar=8, enf=50.0, whiten_fill4=True,
                     random_noise=None):
    """Creates a FIR filter that extracts a carrier,
    applies this filter to signal

    sig        : input signal
    fs         : sampling frequency
    fc         : carrier frequency
    n_cycles   : number of cycles in the wavelet
    bandwidth  : bandwidth of the FIR wavelet filter
                 (used when: bandwidth is not None and n_cycles is None)
    fill       : what to do with the full band signal:
                    0 : keep unchanged
                    1 : filter out the carrier
                    2 : replace the carrier with a white noise
                    3 : replacing with driver[::-1]
                    4 : removing a wide-band around carrier and then use fill=2
    draw       : list of plots
    ordar      : for the whitening with option fill=4

    returns carrier signal and processed full band signal

    """
    if random_noise is None:
        random_noise = np.random.randn(len(sig))

    fir = Carrier()
    fir.design(fs, fc, n_cycles, bandwidth, zero_mean=False)
    if 'c' in draw or 'z' in draw:
        fir.plot(fscale='lin', print_width=False)
    low_sig = fir.direct(sig)

    if fill == 0:
        # keeping driver in high_sig
        high_sig = sig

    elif fill == 1:
        # subtracting driver
        high_sig = sig - low_sig

    elif fill == 2:
        # replacing driver by a white noise
        fill_sig = fir.direct(random_noise)
        fill_sig.shape = sig.shape
        fill_sig *= np.std(low_sig) / np.std(fill_sig)

        high_sig = sig - low_sig + fill_sig

    elif fill == 3:
        # 'replacing with driver[::-1]
        fill_sig = low_sig.ravel()[::-1]
        fill_sig.shape = sig.shape
        high_sig = sig - low_sig + fill_sig

    elif fill == 4:
        # replacing driver by a wide-band white noise

        factor = 4.0
        if n_cycles is not None:
            n_cycles /= factor
        else:
            bandwidth *= factor

        # apply whitening here to have a better filling
        if whiten_fill4:
            white_sig = whiten(sig, fs, ordar, draw=draw, enf=enf)
        else:
            white_sig = sig

        wide_low_sig, high_sig = extract_and_fill(
            sig=white_sig, fs=fs, fc=fc, n_cycles=n_cycles,
            bandwidth=bandwidth, fill=2, draw=draw, enf=enf,
            random_noise=random_noise)

        if 'z' in draw or 'c' in draw:
            plot_multiple_spectrum(
                [white_sig, low_sig, white_sig - low_sig,
                 white_sig - wide_low_sig, wide_low_sig, high_sig],
                labels=['s', 'l', 's-l', 's-wl', 'wl', 'h'], fs=fs,
                colors='bggrrk')
    else:
        raise(ValueError, 'Invalid fill parameter: %s' % str(fill))

    return low_sig, high_sig


def plot_multiple_spectrum(signals, fs, labels, colors):
    """
    plot the signals spectrum
    """
    s = Spectrum(blklen=2048, fs=fs, wfunc=np.blackman)
    for sig in signals:
        s.periodogram(sig, hold=True)
    s.plot(labels=labels, colors=colors, fscale='lin')


def whiten(sig, fs, ordar=8, draw='', enf=50.0, d_enf=1.0,
           zero_phase=True, **kwargs):
    """Use an AR model to whiten a signal
    The whitening filter is not estimated around multiples of
    the electric network frequency (up to d_enf Hz)

    sig   : input signal
    fs    : sampling frequency of input signal
    ordar : order of AR whitening filter
    draw  : list of plots
    enf   : electric network frequency
    denf  : tolerance on electric network frequency
    zero_phase : if True, apply half the whitening for sig(t) and sig(-t)

    returns the whitened signal

    """
    # -------- create the AR model and its spectrum
    ar = Arma(ordar=ordar, ordma=0, fs=fs)
    ar.periodogram(sig)
    # duplicate to see the removal of the electric network frequency
    ar.periodogram(sig, hold=True)

    # -------- remove the influence of the electric network frequency
    k = 1
    # while the harmonic k is included in the spectrum
    while k * (enf - d_enf) < fs / 2.0:
        fmin = k * (enf - d_enf)
        fmax = k * (enf + d_enf)
        kmin = max((0, int(ar.fftlen * fmin / fs)))
        kmax = min(ar.fftlen // 2, int(ar.fftlen * fmax / fs) + 1)
        Amin = ar.psd[-1][0, kmin]
        Amax = ar.psd[-1][0, kmax]
        # linear interpolation between (kmin, Amin) and (kmax, Amax)
        interpol = (Amin * np.linspace(1.0, 0.0, kmax - kmin, endpoint=False) +
                    Amax * np.linspace(0.0, 1.0, kmax - kmin, endpoint=False))

        # remove in positive and negative frequencies
        ar.psd[-1][0, kmin:kmax] = interpol
        ar.psd[-1][0, -kmax:-kmin] = interpol[::-1]

        k += 1

    # -------- change psd for zero phase filtering
    if zero_phase:
        ar.psd[-1] = np.sqrt(ar.psd[-1])

    # -------- estimate the model and apply it
    ar.estimate()

    # apply the whitening twice (forward and backward) for zero-phase filtering
    if zero_phase:
        sigout = ar.inverse(sig)
        sigout = sigout[::-1]
        sigout = ar.inverse(sigout)
        sigout = sigout[::-1]
    else:
        sigout = ar.inverse(sig)

    gain = np.std(sig) / np.std(sigout)
    sigout *= gain

    if 'w' in draw or 'z' in draw:
        ar.arma2psd(hold=True)
        ar.periodogram(sigout, hold=True)
        ar.plot('periodogram before/after whitening',
                labels=['with electric network',
                        'without electric network',
                        'model AR', 'whitened'],
                fscale='lin')
        plt.legend(loc='lower left')

    return sigout


def fill_gap(sig, fs, fa=50.0, dfa=25.0, draw=''):
    """Fill a gap with white noise.
    """
    # -------- get the amplitude of the gap
    sp = Spectrum(blklen=2048, fs=fs, wfunc=np.blackman)
    sp.periodogram(sig)
    fmin = fa - dfa
    fmax = fa + dfa
    kmin = max((0, int(sp.fftlen * fmin / fs)))
    kmax = min(sp.fftlen // 2, int(sp.fftlen * fmax / fs) + 1)
    Amin = sp.psd[-1][0, kmin]
    Amax = sp.psd[-1][0, kmax]
    A_fa = (Amin + Amax) * 0.5

    # -------- bandpass filtering of white noise
    n_cycles = 1.65 * fa / dfa
    fir = Carrier()
    fir.design(fs, fa, n_cycles, None, zero_mean=False)
    fill_sig = fir.direct(np.random.randn(*sig.shape))

    # -------- compute the scale parameter
    sp.periodogram(fill_sig, hold=True)
    kfa = int(sp.fftlen * fa / fs)
    scale = np.sqrt(A_fa / sp.psd[-1][0, kfa])
    fill_sig *= scale

    sig += fill_sig
    if 'g' in draw or 'z' in draw:
        labels = ['signal', 'fill signal', 'gap filled']
        sp.periodogram(sig, hold=True)
        sp.plot(labels=labels, fscale='lin')

    return sig


def show_plot(draw):
    if draw:
        plt.show()


def preprocess(raw_file, fs=1.0, decimation_factor=4, start=None,
               stop=None, enf=50.0, blklen=2048, draw='',
               custom_func=None):
    """Chains the successive steps of the process """
    # ------ read raw signal
    extension = os.path.splitext(raw_file)[1]
    if extension == '.smr':
        sigs, fs, events = smr2sig(raw_file)
    else:
        raise(ValueError, 'invalid extension %s' % extension)

    # ------ remove the beginning or the end of the signal
    if start is not None:
        nmin = max(int(start * fs), 0)
        sigs = [sig[nmin:] for sig in sigs]

        events = {key: value - start for key, value in events.items()}
        if stop is not None:
            stop = stop - start
    if stop is not None:
        nmax = min(int(stop * fs), sigs[0].size)
        sigs = [sig[:nmax] for sig in sigs]

    # -------- decimation
    if decimation_factor is not None:
        fs = fs / decimation_factor
        sigs = [decimate(sig, fs, decimation_factor=decimation_factor)[0]
                for sig in sigs]
        show_plot(draw)

    # -------- reduce noise at 50 or 60 Hz
    if enf is not None:
        hmax = int(0.5 * fs / enf)
        sigs = [dehummer(sig, fs, enf=enf, hmax=hmax, blklen=blklen,
                         draw=draw) for sig in sigs]
        show_plot(draw)

    if custom_func is not None:
        sigs = [custom_func(sig=sig, fs=fs, draw=draw) for sig in sigs]
        show_plot(draw)

    return sigs, fs, events


def extract(sigs, fs, fc_array, n_cycles=None, bandwidth=1.0, fill=0,
            draw='', ordar=8, enf=50.0, whiten_fill4=True,
            random_noise=None, normalize=False, whitening='after'):
    """
    Do fast preprocessing for several values of fc (the driver frequency).

    Parameters
    ----------

    Example
    -------
    for (low_sig, high_sig) in extract_multi(sigs, fs, fc_array):
        pass
    """
    fc_array = np.atleast_1d(fc_array)

    # -------- apply whitening filter
    if whitening == 'before':
        sigs = [whiten(sig, fs=fs, ordar=ordar, draw='') for sig in sigs]
        show_plot(draw)

    if fill in [2, 4]:
        random_noise = np.random.randn(len(sigs[0]))
    else:
        random_noise = None

    # extract_and_fill the driver
    for fc in fc_array:
        low_and_high = [extract_and_fill(
            sig, fs=fs, fc=fc, n_cycles=n_cycles, bandwidth=bandwidth,
            fill=fill, ordar=ordar, enf=enf, whiten_fill4=whitening == 'after',
            random_noise=random_noise) for sig in sigs]
        low_sigs = [both[0] for both in low_and_high]
        high_sigs = [both[1] for both in low_and_high]

        if whitening == 'after' and fill != 4:
            high_sigs = [whiten(high_sig, fs=fs, ordar=ordar, draw='')
                         for high_sig in high_sigs]

        # -------- normalize variances
        if normalize:
            scales = [1.0 / np.std(high_sig) for high_sig in high_sigs]
            high_sigs = [high * s for (high, s) in zip(high_sigs, scales)]
            low_sigs = [low * s for (low, s) in zip(low_sigs, scales)]

        show_plot(draw)

        yield low_sigs, high_sigs
