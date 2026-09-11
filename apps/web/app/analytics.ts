/** Keep sample-derived delay estimates hidden until both analysis gates pass. */
export function canDisplayReliabilityEstimates(
  sufficientHistory: boolean,
  sufficientDelaySamples: boolean,
): boolean {
  return sufficientHistory && sufficientDelaySamples;
}
