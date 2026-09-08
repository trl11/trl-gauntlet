/** Splitting text into its plain runs and the URLs between them. */

/**
 * A URL and the text around it, in the order they appeared.
 *
 * `href` is set on the parts that are links, so a caller renders an anchor for
 * those and the string itself for the rest.
 */
export interface TextPart {
  href?: string;
  text: string;
}

// Trailing punctuation is far more often the sentence's than the URL's, so it
// is left outside the link.
const URL_PATTERN = /https?:\/\/[^\s<>"]*[^\s<>".,;:!?)\]]/g;

/** One string as alternating plain text and links. */
export function linkify(text: string): TextPart[] {
  const parts: TextPart[] = [];
  let index = 0;
  for (const match of text.matchAll(URL_PATTERN)) {
    const start = match.index;
    if (start > index) {
      parts.push({ text: text.slice(index, start) });
    }
    parts.push({ href: match[0], text: match[0] });
    index = start + match[0].length;
  }
  if (index < text.length) {
    parts.push({ text: text.slice(index) });
  }
  return parts;
}
