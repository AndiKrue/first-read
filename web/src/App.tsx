import { FormEvent, useEffect, useLayoutEffect, useRef, useState } from 'react'

import LegalFooter from './legal/LegalFooter'

type Run = {
  run_id: string
  script_title: string
  scene_slug: string
  stage: string
  breakdown: { characters: { name: string }[] } | null
  panel_urls: string[]
  audio_url: string | null
  audio_duration_seconds: number | null
  animatic_url: string | null
  error: string | null
}

type Asset = {
  run_id: string
  asset_type: 'sheet' | 'panel' | 'audio' | 'animatic'
  script_title: string
  scene_slug: string
  characters: string[]
  tone: string
  url: string
}

type Sample = { name: string, title: string, text: string }
type StorySpec = { genre: string, setting: string, characters: string[], situation: string, tone: string }

const emptyStorySpec: StorySpec = { genre: '', setting: '', characters: [], situation: '', tone: '' }
const emptyRun: Run = {
  run_id: '', script_title: '', scene_slug: '', stage: 'ready', breakdown: null,
  panel_urls: [], audio_url: null, audio_duration_seconds: null, animatic_url: null, error: null,
}

function RunFailure({ detail }: { detail: string }) {
  return <div className="run-failure text-sm text-red-300">
    <p data-testid="run-error">The run could not be completed. Try running again.</p>
    <details className="mt-2 text-muted"><summary>Technical details</summary><pre>{detail}</pre></details>
  </div>
}

function MenuIcon() {
  return <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M4 7h16M4 12h16M4 17h16" /></svg>
}

function App() {
  const [title, setTitle] = useState('')
  const [script, setScript] = useState('')
  const [samples, setSamples] = useState<Sample[]>([])
  const [samplesLoading, setSamplesLoading] = useState(true)
  const [run, setRun] = useState<Run>(emptyRun)
  const [previousRuns, setPreviousRuns] = useState<Run[]>([])
  const [runsError, setRunsError] = useState('')
  const [activePanel, setActivePanel] = useState(0)
  const [apiKey, setApiKey] = useState('')
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Asset[]>([])
  const [searching, setSearching] = useState(false)
  const [searchAttempted, setSearchAttempted] = useState(false)
  const [searchError, setSearchError] = useState('')
  const [storySpec, setStorySpec] = useState<StorySpec>(emptyStorySpec)
  const [storyCharacters, setStoryCharacters] = useState('')
  const [writing, setWriting] = useState(false)
  const [storyError, setStoryError] = useState('')
  const [railOpen, setRailOpen] = useState(false)
  const timer = useRef<number | null>(null)
  const screenplay = useRef<HTMLTextAreaElement>(null)
  const isRunning = !['ready', 'done', 'failed'].includes(run.stage)
  const composeBusy = samplesLoading || writing || isRunning
  const cast = run.breakdown?.characters ?? []

  async function loadRuns() {
    setRunsError('')
    try {
      const response = await fetch('/api/runs')
      if (!response.ok) throw new Error('Could not load previous runs')
      setPreviousRuns(await response.json() as Run[])
    } catch (error) {
      setRunsError(error instanceof Error ? error.message : String(error))
    }
  }

  useEffect(() => {
    const controller = new AbortController()
    void fetch('/api/samples', { signal: controller.signal }).then(async (response) => {
      if (!response.ok) throw new Error('Could not load sample scenes')
      const nextSamples = await response.json() as Sample[]
      setSamples(nextSamples)
      if (nextSamples.length) { setTitle(nextSamples[0].title); setScript(nextSamples[0].text) }
    }).catch(() => undefined).finally(() => setSamplesLoading(false))
    void loadRuns()
    return () => { controller.abort(); if (timer.current !== null) window.clearTimeout(timer.current) }
  }, [])

  useLayoutEffect(() => {
    const field = screenplay.current
    const shell = field?.closest<HTMLElement>('.app-layout')
    const context = document.createElement('canvas').getContext('2d')
    if (!field || !shell || !context) return
    const measure = () => {
      const style = getComputedStyle(field)
      context.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`
      const character = context.measureText('0').width + (parseFloat(style.letterSpacing) || 0)
      let chrome = 0
      for (let node: HTMLElement | null = field; node && node !== shell; node = node.parentElement) {
        const css = getComputedStyle(node)
        chrome += parseFloat(css.paddingLeft) + parseFloat(css.paddingRight) + node.offsetWidth - node.clientWidth
      }
      shell.style.setProperty('--compose-width', `${Math.ceil(Math.max(384, 60 * character + chrome))}px`)
    }
    const observer = new ResizeObserver(measure)
    observer.observe(field)
    const fields = field.closest<HTMLElement>('.compose-fields')
    if (fields) observer.observe(fields)
    window.addEventListener('resize', measure)
    let active = true
    void document.fonts.ready.then(() => { if (active) measure() })
    document.fonts.addEventListener('loadingdone', measure)
    measure()
    return () => {
      active = false; observer.disconnect(); window.removeEventListener('resize', measure)
      document.fonts.removeEventListener('loadingdone', measure)
    }
  }, [])

  async function poll(runId: string) {
    const response = await fetch(`/api/runs/${runId}`)
    if (!response.ok) throw new Error('Could not read run status')
    const next = await response.json() as Run
    setRun(next)
    if (next.stage !== 'done' && next.stage !== 'failed') {
      timer.current = window.setTimeout(() => void poll(runId).catch((error: unknown) => {
        setRun((current) => ({ ...current, stage: 'failed', error: error instanceof Error ? error.message : String(error) }))
      }), 1000)
    } else void loadRuns()
  }

  async function hearIt(event: FormEvent) {
    event.preventDefault()
    if (isRunning) return
    if (timer.current !== null) window.clearTimeout(timer.current)
    setRun({ ...emptyRun, script_title: title, stage: 'starting' })
    setActivePanel(0)
    const suppliedKey = apiKey.trim()
    try {
      const headers: Record<string, string> = { 'Content-Type': 'application/json' }
      if (suppliedKey) headers['X-Goog-Api-Key'] = suppliedKey
      const response = await fetch('/api/previz', { method: 'POST', headers, body: JSON.stringify({ script_title: title, fountain_text: script }) })
      setApiKey('')
      if (!response.ok) {
        const payload = await response.json().catch(() => ({})) as { detail?: string }
        throw new Error(payload.detail || 'Could not start the read')
      }
      const { run_id } = await response.json() as { run_id: string }
      await poll(run_id)
    } catch (error) {
      setApiKey('')
      setRun((current) => ({ ...current, stage: 'failed', error: error instanceof Error ? error.message : String(error) }))
    }
  }

  async function randomizeStory() {
    setWriting(true); setStoryError('')
    try {
      const response = await fetch('/api/story/random')
      if (!response.ok) throw new Error('Could not randomize a scene')
      const nextSpec = await response.json() as StorySpec
      setStorySpec(nextSpec); setStoryCharacters(nextSpec.characters.join(', '))
    } catch (error) { setStoryError(error instanceof Error ? error.message : String(error)) }
    finally { setWriting(false) }
  }

  async function writeStory() {
    setWriting(true); setStoryError('')
    try {
      let nextSpec = storySpec
      if (!storySpec.situation.trim() && !storySpec.genre.trim()) {
        const randomResponse = await fetch('/api/story/random')
        if (!randomResponse.ok) throw new Error('Could not choose a scene idea')
        nextSpec = await randomResponse.json() as StorySpec
        setStorySpec(nextSpec); setStoryCharacters(nextSpec.characters.join(', '))
      }
      const characters = storyCharacters.trim() ? storyCharacters.split(',').map((name) => name.trim()).filter(Boolean) : nextSpec.characters
      const response = await fetch('/api/story', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...nextSpec, genre: nextSpec.genre.trim() || 'drama', characters }),
      })
      const payload = await response.json().catch(() => ({})) as { fountain_text?: string, title?: string, detail?: string }
      if (!response.ok || !payload.fountain_text) throw new Error(payload.detail || 'Could not write the scene')
      setScript(payload.fountain_text); if (payload.title) setTitle(payload.title)
    } catch (error) { setStoryError(error instanceof Error ? error.message : String(error)) }
    finally { setWriting(false) }
  }

  function selectSample(sample: Sample) { setTitle(sample.title); setScript(sample.text) }
  function selectRun(selected: Run) {
    if (timer.current !== null) window.clearTimeout(timer.current)
    setRun(selected); setActivePanel(0); setRailOpen(false)
    if (selected.stage !== 'done' && selected.stage !== 'failed') void poll(selected.run_id)
  }
  function newScene() {
    if (timer.current !== null) window.clearTimeout(timer.current)
    setRun(emptyRun); setActivePanel(0); setRailOpen(false)
    requestAnimationFrame(() => document.getElementById('scene-idea')?.focus())
  }
  function syncPanel(currentTime: number) {
    if (!run.audio_duration_seconds || !run.panel_urls.length) return
    setActivePanel(Math.floor(Math.min(currentTime / run.audio_duration_seconds, 0.999999) * run.panel_urls.length))
  }

  async function search(event: FormEvent) {
    event.preventDefault()
    const term = query.trim()
    if (!term) return
    setSearching(true); setSearchAttempted(true); setSearchError('')
    const lowered = term.toLowerCase()
    const spatial = new URLSearchParams()
    if (lowered.includes('exterior')) spatial.set('int_ext', 'EXT')
    if (lowered.includes('interior')) spatial.set('int_ext', 'INT')
    if (lowered.includes('night')) spatial.set('time_of_day', 'NIGHT')
    if (lowered.includes('day')) spatial.set('time_of_day', 'DAY')
    const searches = spatial.toString() ? [spatial] : [new URLSearchParams({ character: term }), new URLSearchParams({ tone: term })]
    try {
      const payloads = await Promise.all(searches.map(async (params) => {
        const response = await fetch(`/api/search?${params}`)
        if (!response.ok) throw new Error('Could not search earlier beats')
        return response.json() as Promise<{ assets: Asset[] }>
      }))
      const unique = new Map<string, Asset>()
      payloads.flatMap((payload) => payload.assets).forEach((asset) => unique.set(`${asset.run_id}:${asset.asset_type}:${asset.url}`, asset))
      setResults([...unique.values()])
    } catch (error) { setSearchError(error instanceof Error ? error.message : String(error)) }
    finally { setSearching(false) }
  }

  return <div className="site-shell">
    <div className="app-layout">
      {railOpen && <button type="button" className="rail-scrim" aria-label="Close workspace rail" onClick={() => setRailOpen(false)} />}
      <aside data-layout-region="rail" className={`rail ${railOpen ? 'rail-open' : ''}`} aria-label="Workspace rail">
        <div className="rail-top"><div className="rail-brand"><h1>FIRST READ</h1><p>Script → voice → frame</p></div><button type="button" className="control-button secondary-button rail-new-scene" onClick={newScene}><span aria-hidden="true">＋</span> New scene</button></div>
        <section data-layout-scroll="rail" className="rail-scroll" tabIndex={0} aria-label="Previous runs">
          <p className="eyebrow">Previous runs</p><div className="rail-gallery">
            {runsError && <div className="error-state"><span>{runsError}</span><button type="button" className="control-button" onClick={() => void loadRuns()}>Retry</button></div>}
            {!runsError && !previousRuns.length && <p className="rail-empty">No runs yet. Choose a sample and run it to begin.</p>}
            {previousRuns.map((previous) => <button key={previous.run_id} type="button" onClick={() => selectRun(previous)} className={`control-button gallery-tile ${previous.run_id === run.run_id ? 'active' : ''}`}>
              {previous.panel_urls.length ? <img src={previous.panel_urls[0]} alt="" /> : <span className="gallery-placeholder">No panel yet</span>}
              <span><strong>{previous.script_title}</strong><small>{previous.scene_slug || 'Awaiting slugline'} · {previous.stage}</small></span>
            </button>)}
          </div>
        </section>
        <div className="rail-bottom">
          <details className="rail-cast surface-panel-subtle"><summary>Cast <span>{cast.length || '—'}</span></summary><div className="cast-list">{cast.length ? cast.map((character) => <span key={character.name}>{character.name}</span>) : <p>Cast appears after the scene is broken down.</p>}</div></details>
          <section className="rail-search-section"><p className="eyebrow">Find an earlier beat</p><form onSubmit={search} className="rail-search"><input value={query} onChange={(event) => setQuery(event.target.value)} className="field min-w-0" placeholder="Character, night, tone" aria-label="Search earlier beats" /><button className="control-button" disabled={searching}>{searching ? '…' : 'Search'}</button></form>
            <div className="rail-results">{searchError && <p role="alert" className="rail-empty">{searchError}</p>}{!searchError && searchAttempted && !searching && !results.length && <p className="rail-empty">No assets match that query.</p>}{results.map((asset) => <article key={`${asset.run_id}:${asset.asset_type}:${asset.url}`} className="search-result">{asset.asset_type === 'panel' ? <img src={asset.url} alt={asset.scene_slug} /> : <span className="gallery-placeholder">{asset.asset_type}</span>}<div><p>{asset.scene_slug}</p><small>{asset.characters.join(', ')} · {asset.tone}</small></div></article>)}</div>
          </section>
        </div>
      </aside>

      <section data-layout-region="compose" className="compose-column" aria-label="Create scene">
        <form onSubmit={hearIt} className="compose-form surface-panel"><div data-layout-scroll="compose" className="compose-fields">
          <section className="story-generator" aria-label="Write me a scene"><label htmlFor="scene-idea"><span className="eyebrow">Write me a scene</span><span>Describe a scene, or leave it empty for a surprise.</span></label><input autoFocus id="scene-idea" value={storySpec.situation} onChange={(event) => setStorySpec({ ...storySpec, situation: event.target.value })} placeholder="A small favor reveals a much larger secret" className="field" /><div className="story-actions"><button type="button" disabled={composeBusy} onClick={() => void writeStory()} className="control-button secondary-button">{writing ? 'Writing…' : 'Write it'}</button><button type="button" disabled={composeBusy} onClick={() => void randomizeStory()} className="control-button secondary-button">Randomize</button></div>{storyError && <span role="alert" className="text-xs text-red-300">{storyError}</span>}
            <details className="surface-panel-subtle"><summary>Scene details</summary><div className="details-fields"><label><span className="eyebrow">Genre</span><input value={storySpec.genre} onChange={(event) => setStorySpec({ ...storySpec, genre: event.target.value })} placeholder="kitchen-sink drama" className="field" /></label><label><span className="eyebrow">Setting</span><input value={storySpec.setting} onChange={(event) => setStorySpec({ ...storySpec, setting: event.target.value })} placeholder="a locksmith's shop" className="field" /></label><label><span className="eyebrow">Characters</span><input value={storyCharacters} onChange={(event) => setStoryCharacters(event.target.value)} placeholder="Mara, Dev" className="field" /></label></div></details>
          </section>
          <section className="compose-samples"><span className="eyebrow">Sample scenes</span><div className="sample-choices">{samples.map((sample) => <button key={sample.name} type="button" disabled={composeBusy} onClick={() => selectSample(sample)} className="control-button sample-button">{sample.name}</button>)}</div></section>
          <label className="field-label"><span className="eyebrow">Scene <span className="scene-format">Fountain format</span></span><textarea ref={screenplay} wrap="off" value={script} onChange={(event) => setScript(event.target.value)} required spellCheck={false} className="field scene-script font-mono text-sm" /></label>
          <label className="field-label"><span className="eyebrow">Script title</span><input value={title} onChange={(event) => setTitle(event.target.value)} required className="field text-lg" /></label>
          <details className="surface-panel-subtle"><summary>Generation access</summary><div className="details-fields"><label><span className="eyebrow">Optional Google API key</span><input type="password" autoComplete="off" value={apiKey} onChange={(event) => setApiKey(event.target.value)} className="field" /></label><p>Used only for this request to bypass the shared demo cap; it is not stored.</p></div></details>
        </div><div className="compose-actions"><p>This produces AI-generated images and synthetic speech.</p><button data-testid="run-button" disabled={composeBusy} className="control-button primary-button">{samplesLoading ? 'Loading samples…' : isRunning ? `Working — ${run.stage}` : 'Hear it'}</button></div></form>
      </section>

      <main data-layout-region="stage" className="stage-shell"><header className="stage-header"><button type="button" className="control-button icon-button rail-toggle" aria-label="Open workspace rail" aria-expanded={railOpen} onClick={() => setRailOpen(true)}><MenuIcon /></button><div className="min-w-0"><p className="eyebrow">{isRunning ? 'In production' : run.run_id ? 'Current run' : 'Workspace'}</p><h1>{run.script_title || title || 'Untitled scene'}</h1></div>{run.run_id && <button type="button" className="control-button stage-edit" onClick={() => document.getElementById('scene-idea')?.focus()}>Edit scene</button>}</header>
        <div data-layout-scroll="stage" className="stage">{!run.run_id && run.stage !== 'failed' && <section className="compose-stage"><div className="compose-intro"><p className="eyebrow">New scene</p><h2>Turn one scene into a first read.</h2><p>Compose, hear the voices, and inspect every frame without losing the thread.</p></div><div className="empty-frame">Your storyboard and performed read will appear here.</div></section>}{(run.run_id || run.stage === 'failed') && <section aria-live="polite" className={`run-status ${isRunning ? 'run-status-active' : ''}`}><div><span className={`status-dot ${isRunning ? 'animate-pulse' : ''}`} /><span>{run.stage}</span></div>{run.error && <RunFailure detail={run.error} />}</section>}
          {(run.run_id || run.panel_urls.length > 0) && <section className="storyboard-surface surface-panel"><div className="surface-heading"><h2>Storyboard</h2><span>{run.panel_urls.length} frames</span></div>{run.panel_urls.length ? <><div className="selected-panel"><img src={run.panel_urls[Math.min(activePanel, run.panel_urls.length - 1)]} alt={`Storyboard panel ${activePanel + 1}`} /></div><div className="panel-strip">{run.panel_urls.map((url, index) => <button key={`${url}:${index}`} type="button" onClick={() => setActivePanel(index)} className={`control-button panel-thumb ${index === activePanel ? 'active' : ''}`} aria-label={`Show panel ${index + 1}`} aria-pressed={index === activePanel}><img src={url} alt="" /><span>Panel {String(index + 1).padStart(2, '0')}</span></button>)}</div></> : <div className="empty-frame">Panels arrive here as they are drawn.</div>}</section>}
        </div>{run.audio_url && <section className="read-surface surface-panel"><p className="eyebrow">The read</p><audio aria-label="Scene table read" controls src={run.audio_url} onTimeUpdate={(event) => syncPanel(event.currentTarget.currentTime)} onEnded={() => setActivePanel(Math.max(run.panel_urls.length - 1, 0))} />{run.animatic_url && <a href={run.animatic_url} download>Download MP4</a>}</section>}</main>
    </div><LegalFooter />
  </div>
}

export default App
