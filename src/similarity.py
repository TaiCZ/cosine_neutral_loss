import collections
from typing import List, Tuple

import numba as nb
import numpy as np
import scipy.sparse
import spectrum_utils.spectrum as sus

import utils


SpectrumTuple = collections.namedtuple(
    "SpectrumTuple", ["precursor_mz", "precursor_charge", "mz", "intensity"]
)


def cosine(
    spectrum1: sus.MsmsSpectrum,
    spectrum2: sus.MsmsSpectrum,
    fragment_mz_tolerance: float,
) -> Tuple[float, List[Tuple[int, int]]]:
    """
    Compute the cosine similarity between the given spectra.

    Parameters
    ----------
    spectrum1 : sus.MsmsSpectrum
        The first spectrum.
    spectrum2 : sus.MsmsSpectrum
        The second spectrum.
    fragment_mz_tolerance : float
        The fragment m/z tolerance used to match peaks.

    Returns
    -------
    Tuple[float, List[Tuple[int, int]]]
        A tuple consisting of (i) the cosine similarity between both spectra,
        and (ii) the indexes of matching peaks in both spectra.
    """
    return _cosine(spectrum1, spectrum2, fragment_mz_tolerance, False)


def modified_cosine(
    spectrum1: sus.MsmsSpectrum,
    spectrum2: sus.MsmsSpectrum,
    fragment_mz_tolerance: float,
) -> Tuple[float, List[Tuple[int, int]]]:
    """
    Compute the modified cosine similarity between the given spectra.

    Parameters
    ----------
    spectrum1 : sus.MsmsSpectrum
        The first spectrum.
    spectrum2 : sus.MsmsSpectrum
        The second spectrum.
    fragment_mz_tolerance : float
        The fragment m/z tolerance used to match peaks.

    Returns
    -------
    Tuple[float, List[Tuple[int, int]]]
        A tuple consisting of (i) the modified cosine similarity between both
        spectra, and (ii) the indexes of matching peaks in both spectra.
    """
    return _cosine(spectrum1, spectrum2, fragment_mz_tolerance, True)


def neutral_loss(
    spectrum1: sus.MsmsSpectrum,
    spectrum2: sus.MsmsSpectrum,
    fragment_mz_tolerance: float,
) -> Tuple[float, List[Tuple[int, int]]]:
    """
    Compute the neutral loss similarity between the given spectra.

    Parameters
    ----------
    spectrum1 : sus.MsmsSpectrum
        The first spectrum.
    spectrum2 : sus.MsmsSpectrum
        The second spectrum.
    fragment_mz_tolerance : float
        The fragment m/z tolerance used to match peaks.

    Returns
    -------
    Tuple[float, List[Tuple[int, int]]]
        A tuple consisting of (i) the neutral loss similarity between both
        spectra, and (ii) the indexes of matching peaks in both spectra.
    """
    # Convert peaks to neutral loss.
    spectrum1 = utils.spec_to_neutral_loss(spectrum1)
    spectrum2 = utils.spec_to_neutral_loss(spectrum2)
    return _cosine(spectrum1, spectrum2, fragment_mz_tolerance, False)


def _cosine(
    spectrum1: sus.MsmsSpectrum,
    spectrum2: sus.MsmsSpectrum,
    fragment_mz_tolerance: float,
    allow_shift: bool,
) -> Tuple[float, List[Tuple[int, int]]]:
    """
    Compute the cosine similarity between the given spectra.

    Parameters
    ----------
    spectrum1 : sus.MsmsSpectrum
        The first spectrum.
    spectrum2 : sus.MsmsSpectrum
        The second spectrum.
    fragment_mz_tolerance : float
        The fragment m/z tolerance used to match peaks.
    allow_shift : bool
        Boolean flag indicating whether to allow peak shifts or not.

    Returns
    -------
    Tuple[float, List[Tuple[int, int]]]
        A tuple consisting of (i) the cosine similarity between both spectra,
        and (ii) the indexes of matching peaks in both spectra.
    """
    spec_tup1 = SpectrumTuple(
        spectrum1.precursor_mz,
        spectrum1.precursor_charge,
        spectrum1.mz,
        np.copy(spectrum1.intensity) / np.linalg.norm(spectrum1.intensity),
    )
    spec_tup2 = SpectrumTuple(
        spectrum2.precursor_mz,
        spectrum2.precursor_charge,
        spectrum2.mz,
        np.copy(spectrum2.intensity) / np.linalg.norm(spectrum2.intensity),
    )
    return _cosine_fast(
        spec_tup1, spec_tup2, fragment_mz_tolerance, allow_shift
    )


@nb.njit
def _cosine_fast(
    spec: SpectrumTuple,
    spec_other: SpectrumTuple,
    fragment_mz_tolerance: float,
    allow_shift: bool,
) -> Tuple[float, List[Tuple[int, int]]]:
    """
    Compute the cosine similarity between the given spectra.

    Parameters
    ----------
    spec : SpectrumTuple
        Numba-compatible tuple containing information from the first spectrum.
    spec_other : SpectrumTuple
        Numba-compatible tuple containing information from the second spectrum.
    fragment_mz_tolerance : float
        The fragment m/z tolerance used to match peaks in both spectra with
        each other.
    allow_shift : bool
        Boolean flag indicating whether to allow peak shifts or not.

    Returns
    -------
    Tuple[float, List[Tuple[int, int]]]
        A tuple consisting of (i) the cosine similarity between both spectra,
        and (ii) the indexes of matching peaks in both spectra.
    """
    # Find the matching peaks between both spectra, optionally allowing for
    # shifted peaks.
    # Candidate peak indices depend on whether we allow shifts
    # (check all shifted peaks as well) or not.
    # Account for unknown precursor charge (default: 1).
    precursor_charge = max(spec.precursor_charge, 1)
    precursor_mass_diff = (
        spec.precursor_mz - spec_other.precursor_mz
    ) * precursor_charge
    # Only take peak shifts into account if the mass difference is relevant.
    num_shifts = 1
    if allow_shift and abs(precursor_mass_diff) >= fragment_mz_tolerance:
        num_shifts += precursor_charge
    other_peak_index = np.zeros(num_shifts, np.uint16)
    mass_diff = np.zeros(num_shifts, np.float32)
    for charge in range(1, num_shifts):
        mass_diff[charge] = precursor_mass_diff / charge

    # Find the matching peaks between both spectra.
    # noinspection PyUnresolvedReferences
    peak_matches = nb.typed.Dict()
    for peak_index, (peak_mz, peak_intensity) in enumerate(
        zip(spec.mz, spec.intensity)
    ):
        # Advance while there is an excessive mass difference.
        for cpi in range(num_shifts):
            while other_peak_index[cpi] < len(spec_other.mz) - 1 and (
                peak_mz - fragment_mz_tolerance
                > spec_other.mz[other_peak_index[cpi]] + mass_diff[cpi]
            ):
                other_peak_index[cpi] += 1
        # Match the peaks within the fragment mass window if possible.
        for cpi in range(num_shifts):
            index = 0
            other_peak_i = other_peak_index[cpi] + index
            while (
                other_peak_i < len(spec_other.mz)
                and abs(
                    peak_mz - (spec_other.mz[other_peak_i] + mass_diff[cpi])
                )
                <= fragment_mz_tolerance
            ):
                peak_matches[(peak_index, other_peak_i)] = \
                    peak_intensity * spec_other.intensity[other_peak_i]
                index += 1
                other_peak_i = other_peak_index[cpi] + index

    # noinspection PyUnresolvedReferences
    data, row_ind, col_ind = nb.typed.List(), nb.typed.List(), nb.typed.List()
    for (row_i, col_i), d in peak_matches.items():
        row_ind.append(row_i)
        col_ind.append(col_i)
        data.append(d)
    with nb.objmode(perm="int32[:]"):
        adj_matrix = scipy.sparse.csr_matrix((data, (row_ind, col_ind)))
        perm = scipy.sparse.csgraph.maximum_bipartite_matching(adj_matrix)
    score, peaks_matched = 0., []
    for col_i, row_i in enumerate(perm):
        if row_i != -1:
            peaks_matched.append((row_i, col_i))
            score += peak_matches[(row_i, col_i)]

    return score, peaks_matched
