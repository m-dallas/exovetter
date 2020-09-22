# -*- coding: utf-8 -*-
"""
Compute Jeff Coughlin's Modshift metrics.

This algorithm is adapted from the Modshift code used in the Kepler
Robovetter and documented on
https://exoplanetarchive.ipac.caltech.edu/docs/KSCI-19105-002.pdf

(see page 30, and appropriate context)

Jeff uses modshift to refer to both the transit significance tests, as
well as a suite of other, related, tests. This code only measures the
metrics for the transit significance measurements. So, for example,
the Odd Even test is not included in this code.

The algorithm is as follows

o Fold and bin the data
o Convolve binned data with model
o Identify the three strongest dips, and the strongest poxsitive excursion
o Remove some of these events, and measure scatter of the rest of the data
o Scale the convolved data by the per-point scatter so that each point
  represents the statistical significance of a transit signal at that phase.
o Record the statistical significance of the 4 events.


@author: fergal
"""

from ipdb import set_trace as idebug
import scipy.special as spspec
import numpy as np


import exovetter.modshift.plotmodshift as plotmodshift
import exovetter.modshift.names as names

def compute_modshift_metrics(time, flux, model, period_days, epoch_days,
                                 duration_hrs):
    """Compute Jeff Coughlin's Modshift metrics.

    This algorithm is adapted from the Modshift code used in the Kepler
    Robovetter and documented on
    https://exoplanetarchive.ipac.caltech.edu/docs/KSCI-19105-002.pdf

    (see page 30, and appropriate context)

    Jeff uses modshift to refer to both the transit significance tests, as
    well as a suite of other, related, tests. This code only measures the
    metrics for the transit significance measurements.

    The algorithm is as follows

    * Fold and bin the data
    * Convolve binned data with model
    * Identify the three strongest dips, and the strongest positive excursion
    * Remove some of these events, and measure scatter of the rest of the data
    * Scale the convolved data by the per-point scatter so that each point
      represents the statistical significance of a transit signal at that phase.
    * Record the statistical significance of the 4 events.

    Inputs
    ------------
    time
        (1d numpy array) times of observations in units of days
    flux
        (1d numpy array) flux values at each time. Flux should be in fractional amplitude
        (with typical out-of-transit values close to zero)
    model
        (1d numpy array) Model transit flux based on the properties of the TCE
        len(model) == len(time)
    period_days, epoch_days, duration_hrs
        (floats) Properties of the transit

    Returns
    -----------
    A dictionary
    """

    assert np.all(np.isfinite(time))
    assert np.all(np.isfinite(flux))
    assert len(time) == len(flux)

    offset = 0.25  #Primary transit at quarter phase, not zero phase
    overres = 10 #Number of bins per transit duration

    numBins = overres * period_days * 24 / duration_hrs
    numBins = int(numBins)

    data = fold_and_bin_data(time, flux, period_days, epoch_days, offset, numBins)
    bphase = data[:, 0]
    bflux = data[:, 1]

    #Fold the model here!
    bModel = fold_and_bin_data(time, model, period_days, epoch_days, offset, numBins)
    bModel[:,1] /= bModel[:,2]  #Avg flux per bin

    conv = compute_convolution_for_binned_data(bphase, bflux, model, offset)
    results = find_indices_of_key_locations(conv, period_days, duration_hrs)

    phi_days = compute_phase(time, period_days, epoch_days, offset)
    sigma = estimate_scatter(
        phi_days, flux, results["pri"], results["sec"], 2 * duration_hrs
    )
    assert sigma > 0
    conv[:, 1] /= sigma  #conv is now in units of statistical signif

    results.update(compute_event_significances(conv, results))
    results["false_alarm_threshold"] = \
        compute_false_alarm_threshold(period_days, duration_hrs)

    if True:
        plotmodshift.plot_modshift(phi_days, flux, model, conv, results)
    return results


"""
#Old, backup way.
def compute_modshift_metrics(time, flux, tce, transitModelFunc):
    assert np.all(np.isfinite(time))
    assert np.all(np.isfinite(flux))
    assert len(time) == len(flux)

    offset = 0.25  #Primary transit at quarter phase, not zero phase
    overres = 10 #Number of bins per transit duration

    period_days = tce[names.PERIOD]
    epoch_days = tce[names.EPOCH]
    duration_hrs = tce[names.DURATION_HRS]

    numBins = overres * period_days * 24 / duration_hrs
    numBins = int(numBins)

    data = fold_and_bin_data(time, flux, period_days, epoch_days, offset, numBins)
    bphase = data[:, 0]
    bflux = data[:, 1]
    model = transitModelFunc(bphase, tce, offset)

    conv = compute_convolution_for_binned_data(bphase, bflux, model, offset)
    results = find_indices_of_key_locations(conv, period_days, duration_hrs)

    phi_days = compute_phase(time, period_days, epoch_days, offset)
    sigma = estimate_scatter(
        phi_days, flux, results["pri"], results["sec"], 2 * duration_hrs
    )
    assert sigma > 0
    conv[:, 1] /= sigma  #conv is now in units of statistical signif

    results.update(compute_event_significances(conv, results))
    results["false_alarm_threshold"] = \
        compute_false_alarm_threshold(period_days, duration_hrs)

    if True:
        plotmodshift.plot_modshift(phi_days, flux, model, conv, results)
    return results
"""

def compute_false_alarm_threshold(period_days, duration_hrs):
    """Compute the stat, significance needed to invalidate the null hypothesis

    An event should be considered statistically significant if its
    peak in the convolved lightcurves is greater than the value computed
    by this function.

    Note that this number is computed on a per-TCE basis. If you are looking
    at many TCEs you will need a stronger threshold. (For example, if
    you use this function to conclude that there is a less than 0.1% chance
    a given event is a false alarm due to Gaussian noise, you expect to
    see one such false alarm in 1,000 TCEs. See Coughlin et al. for the
    formula to ensure less than 1 false alarm over many TCEs.

    Inputs
    ---------
    period_days
        (float) Orbital period
    duration_hrs
        (float) Duration of transit in hours.

    Returns
    --------
    (float) TODO: What exactly is returned. Is this the 1 sigma false
    alarm threshold?
    """
    duration_days = duration_hrs / 24.0

    fa = spspec.erfcinv(duration_days / period_days)
    fa *= np.sqrt(2)
    return fa


def compute_event_significances(conv, results):
    """Compute the statistical significance of 4 major events

    The 4 events are the primary and secondary transits, the "tertiary transit",
    i.e the 3rd most significant dip, and the strongest postive signal.

    These statistical significances are the 4 major computed metrics of
    the modshift test.

    Inputs
    --------
    conv
        (2d np array)  The convolution of the folded lightcurve and the transit model in
        units of statistical significance. Note that
        `compute_convolution_for_binned_data` does NOT return the convolution
        in these units.

    results
        (dict) Contains the indices in `conv` of the 4 events. These indices are
        stored in the keys "pri", "sec", "ter", "pos"

    Returns
    ------------
    The `results` dictionary is returned, with 4 additional keys added,
    'sigma_pri', 'sigma_sec', etc. These contain the statistical significances
    of the 4 major events.
    """

    assert "pri sec ter pos".split() in results.keys()
    out = dict()
    for key in "pri sec ter pos".split():
        i0 = np.argmin(np.fabs(conv[:, 0] - results[key]))
        outKey = f"sigma_{key}"
        out[outKey] = conv[i0, 1]

    return out


def find_indices_of_key_locations(conv, period_days, duration_hrs):
    """Find the locations of the 4 major events in the convolved data

    The 4 major events are the primary transit, the secondary transit,
    the tertiary transit (i.e the 3rd most significant dip), and the
    most postitive event. This function finds their location in the
    folded (and binned) lightcurve convolved with the transit model.

    Inputs
    ---------
    conv
        (2d np array)
        See output of `compute_convolution_for_binned_data`
    period_days, duration_hrs
        (floats)


    Returns
    ----------
    A dictionary. Each value is an index into the conv array.
    """
    out = dict()
    transit_width = duration_hrs / 24.0
    gap_width = 2 * transit_width
    pos_gap_width = 3 * transit_width

    phase = conv[:, 0].copy()
    ms = conv[:, 1].copy()

    i0 = np.argmin(ms)
    out["pri"] = phase[i0]

    idx = phase[i0] - gap_width < phase
    idx &= phase < phase[i0] + gap_width
    ms[idx] = 0

    i1 = np.argmin(ms)
    out["sec"] = phase[i1]

    idx = phase > phase[i1] - gap_width
    idx &= phase < phase[i1] + gap_width
    ms[idx] = 0

    i2 = np.argmin(ms)
    out["ter"] = phase[i2]

    # Gap out 3 transit durations either side of primary and secondary
    # before looking for +ve event
    idx = phase > phase[i0] - pos_gap_width
    idx &= phase < phase[i0] + pos_gap_width
    ms[idx] = 0

    idx = phase > phase[i1] - pos_gap_width
    idx &= phase < phase[i1] + pos_gap_width
    ms[idx] = 0

    i0 = np.argmax(ms)
    out["pos"] = phase[i0]

    return out


def estimate_scatter(phi_days, flux, phi_pri_days, phi_sec_days, gap_width_hrs):
    """
    Estimate the point-to-point scatter in the lightcurve after the
    transits have been removed.

    Inputs
    ---------
    phi_days, flux
        (floats) The folded lightcurve
    phi_pri_days, phi_sec_days
        (floats) Phase of primary and secondary transits, in units of days.
    gap_width_hrs
        (float) How much data on either side of primary and secondary
        transits to gap before computing the point-to-point scatter

    Returns
    ----------
    A float, the rms point-to-point scatter

    TODO: Does Jeff smooth out any residuals in the folded lightcurve
    before computing the scatter?
    """
    assert len(phi_days) == len(flux)

    gap_width_days = gap_width_hrs / 24.0

    # Identfiy points near the primary
    idx1 = phi_pri_days - gap_width_days < phi_days
    idx1 &= phi_days < phi_pri_days + gap_width_days

    # ID points near secondary
    idx2 = phi_sec_days - gap_width_days < phi_days
    idx2 &= phi_days < phi_sec_days + gap_width_days

    # Measure rms of all other points
    idx = ~(idx1 | idx2)
    assert np.any(idx)
    rms = np.std(flux[idx])
    return rms


def compute_convolution_for_binned_data(phase, flux, model, offset_frac):
    """Convolve the binned data with the model

    Inputs
    -----------
    phase, flux
        (1d np arrays) Phase folded and binned lightcurve.
        The phase array should be equally spaced.
    model
        (1d np array) Model transit computed at the same
        phase values as the `phase` array
    offset_frac
        (float). What fraction of the orbital period is the
        primary offset from zero. See `fold_and_bin_data`

    Returns
    -------------
    2d numpy array of convolved data. Columns are phase and flux
    """
    assert np.all(np.isfinite(flux))
    assert np.all(np.isfinite(model))
    assert len(phase) == len(flux)
    assert len(flux) == len(model)

    # Double up the phase and bflux for shifting
    period = np.max(phase)
    phase = np.concatenate([phase, period + phase])
    flux = np.concatenate([flux, flux])
    conv = np.convolve(flux, -model)  # Ensures modshift values are -ve

    i0 = int(1 * len(model))
    i1 = i0 + len(model)
    # i1 = len(conv)
    phi = phase[i0:i1]
    conv = conv[i0:i1]
    #Binned phase, rotated by offset
    bphase = np.fmod(phi - offset_frac * period, period)

    if False:
        #Debugging plots
        plotmodshift._plot_convolution(phase, flux, bphase, conv)
        idebug()
    out = np.vstack([bphase, conv]).transpose()
    return out



def box_car(time, tce, offset=0):
    """The simplest transit model: A point is either fully in or out of transit

    This is a placeholder for a more realistic model to come
    """

    print(tce)
    period = tce[names.PERIOD]
    epoch = offset * period
    duration_days = tce[names.DURATION_HRS] / 24
    depth_frac = tce[names.DEPTH] * 1e-6

    # Make epoch the start of the transit, not the midpoint
    epoch -= duration_days / 2.0

    mnT = np.min(time)
    mxT = np.max(time)

    e0 = int(np.floor((mnT - epoch) / period))
    e1 = int(np.floor((mxT - epoch) / period))

    flux = 0.0 * time
    for i in range(e0, e1 + 1):
        t0 = period * i + epoch
        t1 = t0 + duration_days
        print(i, t0, t1)

        idx = (t0 <= time) & (time <= t1)
        flux[idx] -= depth_frac

    return flux


def fold_and_bin_data(time, flux, period, epoch, num_bins, offset_frac):
    """Fold data, then bin it up.

    Inputs
   ------------
    time
        (1d numpy array) times of observations
    flux
        (1d numpy array) flux values at each time. Flux should be in fractional amplitude
        (with typical out-of-transit values close to zero)
    period
        (float) Orbital Period of TCE. Should be in same units as *time*
    epoch
        (float) Time of first transit of TCE. Same units as *time*
    num_bins
        (int) How many bins to use for folded, binned, lightcurve
    offset_frac
        (float) Fraction of the orbital period to offset the primary transit
        from zero. By default, this function puts the centre of the primary
        transit at phase=0, which makes analysis difficult. Set this to
        0.25 to put the primary transit at 1/4 of the period, which is easier
        to look at, and easier to analyse.
    transit_model_func
        (function) Function that computes the model transit. The signature is
        `func(time, tce, offset=0)`

    Notes
    -----------
    This isn't everything I want it to be. It assumes that every
    element in y, falls entirely in one bin element, which is not
    necessarily true.

    This function does not weight the bins by the number of elements
    contained. This is by design, and makes the computation of the
    statstical significance of events easier. But it does not
    look visually attractive.
    """
    i = np.arange(num_bins)
    bins = i / float(num_bins) * period  # 0..period in numBin steps

    phase = compute_phase(time, period, epoch, offset_frac)
    srt = np.argsort(phase)
    phase = phase[srt]
    flux = flux[srt]

    cts = np.histogram(phase, bins=bins)[0]
    binnedFlux = np.histogram(phase, bins=bins, weights=flux)[0]
    idx = cts > 0

    numNonZeroBins = np.sum(idx)
    out = np.zeros((numNonZeroBins, 2))
    out[:, 0] = bins[:-1][idx]
    out[:, 1] = binnedFlux[idx]
    out[:,2] = cts[idx]
    return out


def compute_phase_for_tce(time, tce, offset=0.25):
    period = tce[names.PERIOD]
    epoch = tce[names.EPOCH]
    return compute_phase(time, period, epoch, offset)


def compute_phase(time, period, epoch, offset=0.25):
    return np.fmod(time - epoch + offset * period, period)


"""
    #Jeff's way of doing it. I don't think I want to do it that way
  for(i=0;i<ndat;i++)  // Perform ndat pertubations
    {
    tmpsum2 = 0;

    // Before transit, can look up values for compuatation speed increase
    for(j=0;j<startti;j++)
      tmpsum2 += flat[j+i];

    // Compute new values inside transit
    for(j=startti;j<endti;j++)
     // Shitfing data, holding model steady. Moving data points backwards,
     //or model forwards, same thing
      tmpsum2 += pow(data[j+i].flux - data[j].model,2);

    // After transit, can look up values for computation speed increase
    for(j=endti;j<ndat;j++)
      tmpsum2 += flat[j+i];

    rms[i] = sqrt(tmpsum2/ndat);  // RMS of the new residuals
    if(rms[i] < rmsmin)
      rmsmin = rms[i];
    }

"""
