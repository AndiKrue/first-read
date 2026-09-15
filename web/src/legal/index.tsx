import Contact from './Contact'
import Impressum from './Impressum'
import PrivacyPolicy from './PrivacyPolicy'

export function legalPage(pathname: string) {
  if (pathname === '/impressum') return <Impressum />
  if (pathname === '/privacy') return <PrivacyPolicy />
  if (pathname === '/contact') return <Contact />
  return null
}
