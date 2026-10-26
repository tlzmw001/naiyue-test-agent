/** Delays before the three read/subscription retries. These do not limit model or tool execution. */
export const runReconnectDelays = [250, 1000, 3000] as const;
/** Visible-page metadata polling; selected output is read only while its panel is open. */
export const backgroundTaskPollMs = 3000;

/** Idle-page discovery of other clients uses read-only metadata requests. */
export const sessionSyncPollMs = 3000;
