import { highlightCode, type HighlightRequest } from './code-highlight';

self.onmessage = (event: MessageEvent<HighlightRequest>) => {
  self.postMessage(highlightCode(event.data));
};
