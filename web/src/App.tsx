import { FormEvent, useEffect, useRef, useState } from 'react'

type Run = {
  run_id: string
  script_title: string
  scene_slug: string
  stage: string
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

type Sample = {
  name: string
  title: string
  text: string
}

type StorySpec = {
  genre: string
  setting: string
  characters: string[]
  situation: string
  tone: string
}

const emptyStorySpec: StorySpec = {
  genre: '',
  setting: '',
  characters: [],
  situation: '',
  tone: '',
}

const emptyRun: Run = {
  run_id: '',
  script_title: '',
  scene_slug: '',
  stage: 'ready',
  panel_urls: [],
  audio_url: null,
  audio_duration_seconds: null,
  animatic_url: null,
  error: null,
}

function App() {
  const [title, setTitle] = useState('')
  const [script, setScript] = useState('')
  const [samples, setSamples] = useState<Sample[]>([])
  const [run, setRun] = useState<Run>(emptyRun)
  const [previousRuns, setPreviousRuns] = useState<Run[]>([])
  const [activePanel, setActivePanel] = useState(0)
  const [apiKey, setApiKey] = useState('')
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Asset[]>([])
  const [searching, setSearching] = useState(false)
  const [storySpec, setStorySpec] = useState<StorySpec>(emptyStorySpec)
  const [storyCharacters, setStoryCharacters] = useState('')
  const [writing, setWriting] = useState(false)
  const [storyError, setStoryError] = useState('')
  const timer = useRef<number | null>(null)
  const isRunning = !['ready', 'done', 'failed'].includes(run.stage)

  async function loadRuns() {
    const response = await fetch('/api/runs')
    if (response.ok) setPreviousRuns(await response.json() as Run[])
  }

  useEffect(() => {
    void fetch('/api/samples').then(async (response) => {
      if (!response.ok) return
      const nextSamples = await response.json() as Sample[]
      setSamples(nextSamples)
      if (nextSamples.length) {
        setTitle(nextSamples[0].title)
        setScript(nextSamples[0].text)
      }
    })
    void loadRuns()
    return () => {
      if (timer.current !== null) window.clearTimeout(timer.current)
    }
  }, [])

  async function poll(runId: string) {
    const response = await fetch(`/api/runs/${runId}`)
    if (!response.ok) throw new Error('Could not read run status')
    const next: Run = await response.json()
    setRun(next)
    if (next.stage !== 'done' && next.stage !== 'failed') {
      timer.current = window.setTimeout(() => void poll(runId), 1000)
    } else {
      void loadRuns()
    }
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
      const response = await fetch('/api/previz', {
        method: 'POST',
        headers,
        body: JSON.stringify({ script_title: title, fountain_text: script }),
      })
      setApiKey('')
      if (!response.ok) {
        const payload = await response.json().catch(() => ({})) as { detail?: string }
        throw new Error(payload.detail || 'Could not start the read')
      }
      const { run_id } = await response.json()
      await poll(run_id)
    } catch (error) {
      setApiKey('')
      setRun({ ...emptyRun, script_title: title, stage: 'failed', error: error instanceof Error ? error.message : String(error) })
    }
  }

  async function randomizeStory() {
    setWriting(true)
    setStoryError('')
    try {
      const response = await fetch('/api/story/random')
      if (!response.ok) throw new Error('Could not randomize a scene')
      const nextSpec = await response.json() as StorySpec
      setStorySpec(nextSpec)
      setStoryCharacters(nextSpec.characters.join(', '))
    } catch (error) {
      setStoryError(error instanceof Error ? error.message : String(error))
    } finally {
      setWriting(false)
    }
  }

  async function writeStory() {
    if (!storySpec.genre.trim()) {
      setStoryError('Choose or enter a genre first.')
      return
    }
    setWriting(true)
    setStoryError('')
    try {
      const response = await fetch('/api/story', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...storySpec,
          characters: storyCharacters.split(',').map((name) => name.trim()).filter(Boolean),
        }),
      })
      const payload = await response.json().catch(() => ({})) as { fountain_text?: string, title?: string, detail?: string }
      if (!response.ok || !payload.fountain_text) throw new Error(payload.detail || 'Could not write the scene')
      setScript(payload.fountain_text)
      if (payload.title) setTitle(payload.title)
    } catch (error) {
      setStoryError(error instanceof Error ? error.message : String(error))
    } finally {
      setWriting(false)
    }
  }

  function selectSample(sample: Sample) {
    setTitle(sample.title)
    setScript(sample.text)
  }

  function selectRun(selected: Run) {
    if (timer.current !== null) window.clearTimeout(timer.current)
    setRun(selected)
    setActivePanel(0)
    if (selected.stage !== 'done' && selected.stage !== 'failed') void poll(selected.run_id)
  }

  function syncPanel(currentTime: number) {
    if (!run.audio_duration_seconds || !run.panel_urls.length) return
    const fraction = Math.min(currentTime / run.audio_duration_seconds, 0.999999)
    setActivePanel(Math.floor(fraction * run.panel_urls.length))
  }

  async function search(event: FormEvent) {
    event.preventDefault()
    const term = query.trim()
    if (!term) return
    setSearching(true)
    const lowered = term.toLowerCase()
    const spatial = new URLSearchParams()
    if (lowered.includes('exterior')) spatial.set('int_ext', 'EXT')
    if (lowered.includes('interior')) spatial.set('int_ext', 'INT')
    if (lowered.includes('night')) spatial.set('time_of_day', 'NIGHT')
    if (lowered.includes('day')) spatial.set('time_of_day', 'DAY')
    const searches = spatial.toString()
      ? [spatial]
      : [new URLSearchParams({ character: term }), new URLSearchParams({ tone: term })]
    try {
      const payloads = await Promise.all(
        searches.map(async (params) => {
          const response = await fetch(`/api/search?${params}`)
          if (!response.ok) throw new Error('Production memory query failed')
          return response.json() as Promise<{ assets: Asset[] }>
        }),
      )
      const unique = new Map<string, Asset>()
      payloads.flatMap((payload) => payload.assets).forEach((asset) => {
        unique.set(`${asset.run_id}:${asset.asset_type}:${asset.url}`, asset)
      })
      setResults([...unique.values()])
    } finally {
      setSearching(false)
    }
  }

  return (
    <main className="mx-auto min-h-screen max-w-7xl px-5 py-8 md:px-10 md:py-12">
      <header className="mb-12 flex items-baseline justify-between border-b border-white/15 pb-5">
        <h1 className="text-xl font-semibold tracking-[0.24em]">FIRST READ</h1>
        <p className="font-mono text-xs uppercase tracking-widest text-subtle">Script → voice → frame</p>
      </header>

      <form onSubmit={hearIt} className="grid gap-5">
        <label className="grid gap-2">
          <span className="eyebrow">Script title</span>
          <input value={title} onChange={(event) => setTitle(event.target.value)} required className="field text-lg" />
        </label>
        <details className="border border-white/15 bg-white/[0.02]">
          <summary className="cursor-pointer px-4 py-3 text-sm text-muted hover:text-white">Write me a scene</summary>
          <div className="grid gap-4 border-t border-white/10 p-4 md:grid-cols-2">
            <label className="grid gap-2">
              <span className="eyebrow">Genre</span>
              <input value={storySpec.genre} onChange={(event) => setStorySpec({ ...storySpec, genre: event.target.value })} placeholder="kitchen-sink drama" className="field" />
            </label>
            <label className="grid gap-2">
              <span className="eyebrow">Setting</span>
              <input value={storySpec.setting} onChange={(event) => setStorySpec({ ...storySpec, setting: event.target.value })} placeholder="a locksmith's shop at closing time" className="field" />
            </label>
            <label className="grid gap-2">
              <span className="eyebrow">Characters</span>
              <input value={storyCharacters} onChange={(event) => setStoryCharacters(event.target.value)} placeholder="Mara, Dev" className="field" />
            </label>
            <label className="grid gap-2">
              <span className="eyebrow">Situation</span>
              <input value={storySpec.situation} onChange={(event) => setStorySpec({ ...storySpec, situation: event.target.value })} placeholder="a small favor reveals a much larger secret" className="field" />
            </label>
            <div className="flex flex-wrap items-center gap-3 md:col-span-2">
              <button type="button" disabled={writing} onClick={() => void randomizeStory()} className="tap-target border border-white/25 px-4 py-2 text-xs uppercase tracking-wider hover:border-signal disabled:opacity-70">Randomize</button>
              <button type="button" disabled={writing || !storySpec.genre.trim()} onClick={() => void writeStory()} className="tap-target bg-white/10 px-4 py-2 text-xs uppercase tracking-wider hover:bg-signal hover:text-ink disabled:opacity-70">{writing ? 'Writing…' : 'Write it'}</button>
              {storyError && <span role="alert" className="text-xs text-red-300">{storyError}</span>}
            </div>
          </div>
        </details>
        <div>
          <span className="eyebrow">Sample scenes</span>
          <div className="mt-2 flex flex-wrap gap-2">
            {samples.map((sample) => (
              <button key={sample.name} type="button" onClick={() => selectSample(sample)} className="tap-target border border-white/20 px-3 py-2 font-mono text-xs text-muted hover:border-signal hover:text-white">{sample.name}</button>
            ))}
          </div>
        </div>
        <label className="grid gap-2">
          <span className="eyebrow">One Fountain scene</span>
          <textarea value={script} onChange={(event) => setScript(event.target.value)} required spellCheck={false} className="field min-h-[25rem] resize-y font-mono text-sm leading-7" />
        </label>
        <div className="grid max-w-xl gap-2">
          <label className="eyebrow" htmlFor="api-key">Optional Google API key</label>
          <input id="api-key" type="password" autoComplete="off" value={apiKey} onChange={(event) => setApiKey(event.target.value)} className="field" />
          <p className="text-xs text-subtle">Used only for this generation request to bypass the shared demo cap; it is not stored.</p>
        </div>
        <button disabled={isRunning} className="tap-target w-fit bg-signal px-7 py-3 text-sm font-bold uppercase tracking-widest text-ink transition-opacity disabled:opacity-70">
          {isRunning ? `Working — ${run.stage}` : 'Hear it'}
        </button>
      </form>

      <section aria-live="polite" className="my-12 border-y border-white/15 py-5">
        <div className="flex items-center gap-3">
          <span className={`status-dot ${isRunning ? 'animate-pulse' : ''}`} />
          <span className="font-mono text-sm uppercase tracking-widest">{run.stage}</span>
        </div>
        {run.error && <p className="mt-3 text-sm text-red-300">{run.error}</p>}
      </section>

      <section className="mb-14">
        <div className="mb-4 flex items-end justify-between">
          <h2 className="section-title">Storyboard</h2>
          <span className="font-mono text-xs text-subtle">{run.panel_urls.length} frames</span>
        </div>
        <div className="panel-strip">
          {run.panel_urls.map((url, index) => (
            <button key={url} onClick={() => setActivePanel(index)} className={`panel-thumb ${index === activePanel ? 'active' : ''}`} aria-label={`Show panel ${index + 1}`}>
              <img src={url} alt={`Storyboard panel ${index + 1}`} />
              <span>{String(index + 1).padStart(2, '0')}</span>
            </button>
          ))}
          {!run.panel_urls.length && <div className="empty-frame">Panels arrive here as they are drawn.</div>}
        </div>
      </section>

      {run.audio_url && run.panel_urls.length > 0 && (
        <section className="mb-16 grid gap-5 lg:grid-cols-[2fr_1fr]">
          <img className="aspect-video w-full bg-black object-contain" src={run.panel_urls[activePanel]} alt={`Playing panel ${activePanel + 1}`} />
          <div className="flex flex-col justify-end border border-white/15 p-5">
            <p className="eyebrow mb-6">The read</p>
            <audio className="w-full" controls src={run.audio_url} onTimeUpdate={(event) => syncPanel(event.currentTarget.currentTime)} onEnded={() => setActivePanel(run.panel_urls.length - 1)} />
            {run.animatic_url && <a className="mt-6 text-sm text-signal underline underline-offset-4" href={run.animatic_url} download>Download muxed MP4</a>}
          </div>
        </section>
      )}

      <section className="border-t border-white/15 pt-10">
        <p className="eyebrow mb-3">Querying the production memory</p>
        <h2 className="section-title mb-6">Find an earlier beat</h2>
        <form onSubmit={search} className="flex max-w-2xl gap-3">
          <input value={query} onChange={(event) => setQuery(event.target.value)} className="field min-w-0 flex-1" placeholder="Character name, night exterior, or tone" />
          <button disabled={searching} className="tap-target border border-white/30 px-5 text-sm uppercase tracking-wider hover:border-signal disabled:opacity-70">Search</button>
        </form>
        <div className="mt-7 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {results.map((asset) => (
            <article key={`${asset.run_id}:${asset.asset_type}:${asset.url}`} className="border border-white/15 bg-white/[0.03]">
              {asset.asset_type === 'panel' ? <img src={asset.url} alt={asset.scene_slug} className="aspect-video w-full object-cover" /> : <div className="empty-frame aspect-video">{asset.asset_type}</div>}
              <div className="p-4">
                <p className="font-mono text-xs text-signal">{asset.scene_slug}</p>
                <p className="mt-2 text-sm text-muted">{asset.characters.join(', ')} · {asset.tone}</p>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="mt-16 border-t border-white/15 pt-10">
        <p className="eyebrow mb-3">Replay without regenerating</p>
        <h2 className="section-title mb-6">Previous runs</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {previousRuns.map((previous) => (
            <button key={previous.run_id} type="button" onClick={() => selectRun(previous)} className="overflow-hidden border border-white/15 bg-white/[0.03] text-left hover:border-signal">
              {previous.panel_urls.length ? <img src={previous.panel_urls[0]} alt="" className="aspect-video w-full object-cover" /> : <div className="empty-frame aspect-video">No panel yet</div>}
              <div className="p-4">
                <p className="text-base">{previous.script_title}</p>
                <p className="mt-2 font-mono text-xs text-signal">{previous.scene_slug || 'Awaiting slugline'}</p>
                <p className="mt-2 text-xs uppercase tracking-wider text-subtle">{previous.stage}</p>
              </div>
            </button>
          ))}
        </div>
      </section>
    </main>
  )
}

export default App
