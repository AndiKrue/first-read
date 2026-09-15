import type { ReactNode } from 'react'

import LegalFooter from './LegalFooter'

export default function LegalPage({ title, children }: { title: string, children: ReactNode }) {
  return <div className="legal-shell"><header className="legal-header"><a className="legal-brand" href="/">FIRST READ</a><span>Legal information</span></header><main className="legal-page"><p className="eyebrow">FIRST READ</p><h1>{title}</h1>{children}</main><LegalFooter /></div>
}
