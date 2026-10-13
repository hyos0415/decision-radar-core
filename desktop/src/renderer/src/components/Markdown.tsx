import ReactMarkdown, { type Components } from 'react-markdown'
import remarkFrontmatter from 'remark-frontmatter'
import remarkGfm from 'remark-gfm'
import { cn } from '@renderer/lib/utils'

// 원시 HTML은 그리지 않는다(rehype-raw 없음) — 본문에 모델이 쓴 초안이 들어간다.
// front matter(--- … ---)는 remark-frontmatter가 yaml 노드로 떼어내고, 그릴 요소가 없어 사라진다.
const REMARK = [remarkFrontmatter, remarkGfm]

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
      <ReactMarkdown remarkPlugins={REMARK} components={COMPONENTS}>
        {children}
      </ReactMarkdown>
    </div>
  )
}
