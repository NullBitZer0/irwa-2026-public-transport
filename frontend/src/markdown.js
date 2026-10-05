/**
 * Minimal markdown renderer for agent replies.
 *
 * The agents emit a small, predictable subset of markdown: **bold**, `code`,
 * bullet lists and blank-line paragraphs. Text is HTML-escaped first, so this
 * cannot inject markup from user input.
 */

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/** Inline formatting: `code` and **bold**. */
function renderInline(text) {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|\s)\*([^*\n]+)\*/g, '$1<em>$2</em>')
}

/**
 * Convert a markdown reply into React-safe HTML.
 * @param {string} markdown
 * @returns {string} HTML string
 */
export function renderMarkdown(markdown) {
  if (!markdown) return ''

  const lines = markdown.split('\n')
  const out = []
  let listItems = []

  const flushList = () => {
    if (listItems.length) {
      out.push(`<ul>${listItems.map((li) => `<li>${renderInline(li)}</li>`).join('')}</ul>`)
      listItems = []
    }
  }

  for (const rawLine of lines) {
    const line = rawLine.trimEnd()

    if (!line.trim()) {
      flushList()
      continue
    }

    const bullet = line.match(/^\s*[-*]\s+(.*)$/)
    if (bullet) {
      listItems.push(bullet[1])
      continue
    }

    flushList()
    out.push(`<p>${renderInline(line)}</p>`)
  }

  flushList()
  return out.join('')
}