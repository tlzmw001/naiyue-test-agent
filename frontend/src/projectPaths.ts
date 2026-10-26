/** Preserve the real path in requests; expose invisible controls in labels. */
export function displayProjectPath(value: string) {
  return value.replace(/[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/g,
    character => '\\u' + character.charCodeAt(0).toString(16).padStart(4, '0'));
}
