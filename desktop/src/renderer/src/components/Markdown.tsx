import ReactMarkdown, { type Components, type Options } from 'react-markdown'
import remarkFrontmatter from 'remark-frontmatter'
import remarkGfm from 'remark-gfm'
import { cn } from '@renderer/lib/utils'

// 원시 HTML은 그리지 않는다(rehype-raw 없음) — 본문에 모델이 쓴 초안이 들어간다.
const REMARK = [remarkFrontmatter, remarkGfm]

// front matter(--- … ---)는 remark-frontmatter가 yaml 노드로 떼어낸다. 화면 머리에 없는 값
// (evidence · revisit_when 등)도 있으니 지우지 않고 접힌 블록으로 원문 그대로 둔다.
const REMARK_REHYPE: Options['remarkRehypeOptions'] = {
  handlers: {
    yaml: (_state, node: { value: string }) => ({
      type: 'element',
      tagName: 'details',
      properties: { className: ['front-matter'] },
      children: [
        {
          type: 'element',
          tagName: 'summary',
          properties: {},
          children: [{ type: 'text', value: 'front matter' }]
        },
        {
          type: 'element',
          tagName: 'pre',
          properties: {},
          children: [
            {
              type: 'element',
              tagName: 'code',
              properties: { className: ['language-yaml'] },
              children: [{ type: 'text', value: node.value }]
            }
          ]
        }
      ]
    })
  }
}

// 링크는 새 창으로 요청한다 — main 프로세스가 막고 http(s)만 시스템 브라우저로 연다.
const COMPONENTS: Components = {
  a: ({ href, title, children }) => (
    <a href={href} title={title} target="_blank" rel="noreferrer">
      {children}
    </a>
  )
}

// 스타일은 assets/design.css의 .document 아래에 있다
export function Markdown({
  children,
  compact = false
}: {
  children: string
  compact?: boolean
}): React.JSX.Element {
  return (
    <div className={cn('document', compact && 'compact')}>
      <ReactMarkdown
        remarkPlugins={REMARK}
        remarkRehypeOptions={REMARK_REHYPE}
        components={COMPONENTS}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}
