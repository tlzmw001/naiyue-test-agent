/** Delays before the three read/subscription retries. These do not limit model or tool execution. */
export const runReconnectDelays = [250, 1000, 3000] as const;
