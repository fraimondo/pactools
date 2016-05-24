#!/usr/bin/python
"""
Estimation of ARMA model

The class Arma is derived from the class Spectrum:
the class Spectrum allows the estimation of a power spectral
density, from which the class Arma allows the estimation of the
correlation function and then the parameters of the model

This module also contains functions that process AR models:
ai2ki : convert AR coefficients to partial correlations
ki2ai : convert parcor coefficients to autoregressive ones

"""
from __future__ import print_function

import numpy as np
from scipy import signal, linalg, fftpack

from .spectrum import Spectrum


class Arma(Spectrum):
    def __init__(self, ordar=2, ordma=0, **kargs):
        """Create an estimator of ARMA model:
        y(t) + a(1)y(t-1) + ... + a(ordar)y(t-ordar) =
        b(0)e(t) + b(1)e(t-1) + ... + b(ordma)e(t-ordma)

        ordar : order of the autogressive part
        ordma :  order of the moving average part

        """
        Spectrum.__init__(self, **kargs)
        self.ordar = ordar
        self.ordma = ordma

    def estimate(self, nbcorr=np.nan, numpsd=-1):
        if np.isnan((nbcorr)):
            nbcorr = self.ordar

        # -------- estimate correlation from psd
        correl = fftpack.ifft(self.psd[numpsd][0], self.fftlen, 0).real

        # -------- estimate AR part
        col1 = correl[self.ordma:self.ordma + nbcorr]
        row1 = correl[np.abs(np.arange(self.ordma,
                                       self.ordma - self.ordar,
                                       -1))]
        R = linalg.toeplitz(col1, row1)
        r = -correl[self.ordma + 1:self.ordma + nbcorr + 1]
        AR = linalg.solve(R, r)
        self.AR_ = AR

        # -------- estimate correlation of MA part

        # -------- estimate MA part
        if self.ordma == 0:
            sigma2 = correl[0] + np.dot(AR, correl[1:self.ordar + 1])
            self.MA = np.ones(1) * np.sqrt(sigma2)
        else:
            raise NotImplementedError(
                'arma: estimation of the MA part not yet implemented')

    def arma2psd(self, hold=False):
        """Compute the power spectral density of the ARMA model

        """
        arpart = np.concatenate((np.ones(1), self.AR_))
        psdar = np.abs(fftpack.fft(arpart, self.fftlen, 0)) ** 2
        psdma = np.abs(fftpack.fft(self.MA, self.fftlen, 0)) ** 2
        psd = psdma / psdar
        if not hold:
            self.psd = []
        self.psd.append(psd[None, :])

    def inverse(self, sigin):
        """Apply the inverse ARMA filter to a signal

        sigin : input signal (ndarray)

        returns the filtered signal(ndarray)

        """
        arpart = np.concatenate((np.ones(1), self.AR_))
        return signal.fftconvolve(sigin, arpart, 'same')


def ai2ki(ar):
    """Convert AR coefficients to partial correlations
    (inverse Levinson recurrence)

    ar : AR models stored by columns

    returns the partial correlations (one model by column)

    """
    parcor = np.copy(ar)
    ordar, _ = ar.shape
    for i in range(ordar - 1, -1, -1):
        if i > 0:
            parcor[0:i, :] -= parcor[i:i + 1, :] * np.flipud(parcor[0:i, :])
            parcor[0:i, :] *= 1.0 / (1.0 - parcor[i:i + 1, :] ** 2)
    return parcor


def ki2ai(parcor):
    """Convert parcor coefficients to autoregressive ones
    (Levinson recurrence)

    parcor : partial correlations stored by columns

    returns the AR models by columns

    """
    ar = np.zeros_like(parcor)
    ordar, _ = parcor.shape
    for i in range(ordar):
        if i > 0:
            ar[0:i, :] += parcor[i:i + 1, :] * np.flipud(ar[0:i, :])
        ar[i, :] = parcor[i, :]

    # ok, at least in stationary models
    return ar
