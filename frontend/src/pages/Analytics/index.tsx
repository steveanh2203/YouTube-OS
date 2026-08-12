import { useEffect, useState } from 'react'
import {
  ArrowUpRight,
  CheckCircle2,
  Gauge,
  KeyRound,
  Lightbulb,
  Radar,
  RefreshCw,
  Search,
  ShieldAlert,
  Sparkles,
  TrendingUp,
  Users,
} from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  nicheResearchApi,
  type NicheResearchResponse,
  type NicheResearchSummary,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import { usePanelContext } from '@/contexts/PanelContext'


type VideoDuration = 'any' | 'short' | 'medium' | 'long'

const REGION_OPTIONS = [
  { value: 'US', label: 'United States' },
  { value: 'GB', label: 'United Kingdom' },
  { value: 'CA', label: 'Canada' },
  { value: 'AU', label: 'Australia' },
  { value: 'VN', label: 'Vietnam' },
]

const LANGUAGE_OPTIONS = [
  { value: 'en', label: 'English' },
  { value: 'vi', label: 'Vietnamese' },
  { value: 'es', label: 'Spanish' },
  { value: 'de', label: 'German' },
  { value: 'fr', label: 'French' },
]

const PERIOD_OPTIONS = [
  { value: 30, label: 'Last 30 days' },
  { value: 90, label: 'Last 90 days' },
  { value: 365, label: 'Last 12 months' },
  { value: 730, label: 'Last 2 years' },
]

const DURATION_OPTIONS: { value: VideoDuration; label: string }[] = [
  { value: 'any', label: 'Any duration' },
  { value: 'short', label: 'Under 4 minutes' },
  { value: 'medium', label: '4–20 minutes' },
  { value: 'long', label: 'Over 20 minutes' },
]

function getErrorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback
}

function formatCompact(value: number) {
  return new Intl.NumberFormat('en-US', {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(value)
}

function formatDate(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(date)
}

function scoreTone(score: number) {
  if (score >= 70) return 'text-emerald-300'
  if (score >= 45) return 'text-amber-300'
  return 'text-rose-300'
}

function SummaryCard({
  label,
  value,
  detail,
  icon: Icon,
  valueClassName,
}: {
  label: string
  value: string
  detail: string
  icon: React.ElementType
  valueClassName?: string
}) {
  return (
    <div className="card gap-0 p-4">
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs font-semibold uppercase tracking-[0.08em] text-surface-500">{label}</span>
        <Icon size={16} className="text-surface-500" />
      </div>
      <p className={cn('mt-3 font-heading text-2xl font-semibold tracking-[-0.04em] text-surface-900', valueClassName)}>
        {value}
      </p>
      <p className="mt-1 text-xs text-surface-500">{detail}</p>
    </div>
  )
}

function ResultsOverview({ summary }: { summary: NicheResearchSummary }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <SummaryCard
        label="Opportunity"
        value={`${summary.opportunity_score}/100`}
        detail="Demand, competition, velocity & breakout"
        icon={Sparkles}
        valueClassName={scoreTone(summary.opportunity_score)}
      />
      <SummaryCard
        label="Median views"
        value={formatCompact(summary.median_views)}
        detail={`Across ${summary.result_count} relevant videos`}
        icon={TrendingUp}
      />
      <SummaryCard
        label="Median velocity"
        value={`${formatCompact(summary.median_views_per_day)}/day`}
        detail="Views normalized by video age"
        icon={Gauge}
      />
      <SummaryCard
        label="Competition"
        value={`${summary.competition_score}/100`}
        detail={`${summary.small_channel_breakout_rate}% small-channel breakout`}
        icon={Users}
        valueClassName={scoreTone(100 - summary.competition_score)}
      />
    </div>
  )
}

export default function Analytics() {
  const { setMainView } = usePanelContext()
  const [settingsLoading, setSettingsLoading] = useState(true)
  const [keyConnected, setKeyConnected] = useState(false)
  const [query, setQuery] = useState('')
  const [region, setRegion] = useState('US')
  const [language, setLanguage] = useState('en')
  const [period, setPeriod] = useState(365)
  const [duration, setDuration] = useState<VideoDuration>('any')
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<NicheResearchResponse | null>(null)

  useEffect(() => {
    let cancelled = false

    void nicheResearchApi.settings()
      .then((response) => {
        if (!cancelled) setKeyConnected(response.settings.connected)
      })
      .catch(() => {
        if (!cancelled) setKeyConnected(false)
      })
      .finally(() => {
        if (!cancelled) setSettingsLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [])

  const runResearch = async () => {
    const normalizedQuery = query.trim()
    if (normalizedQuery.length < 2 || searching) return

    setSearching(true)
    setError('')
    try {
      const response = await nicheResearchApi.search({
        query: normalizedQuery,
        region_code: region,
        relevance_language: language,
        published_within_days: period,
        video_duration: duration,
        max_results: 25,
      })
      setResult(response)
    } catch (searchError: unknown) {
      setError(getErrorMessage(searchError, 'Niche research failed. Please try again.'))
    } finally {
      setSearching(false)
    }
  }

  return (
    <div className="flex h-full flex-col bg-background">
      <div className="border-b border-surface-200 bg-surface-0/85 px-6 py-4 backdrop-blur">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <h1 className="page-title">Niche Research</h1>
              <Badge variant="outline" className="border-primary-500/20 bg-primary-500/10 text-primary-300">
                YouTube data
              </Badge>
            </div>
            <p className="page-sub mt-0.5">Find underserved topics using public demand, velocity, and competition signals.</p>
          </div>
          <p className="max-w-lg text-xs leading-relaxed text-surface-500">
            Directional research only. Scores are calculated from public YouTube results, not estimated search volume.
          </p>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 md:p-6">
        <div className="mx-auto max-w-6xl space-y-4">
          {settingsLoading ? (
            <div className="card flex min-h-56 items-center justify-center gap-2 p-8 text-sm text-surface-500">
              <RefreshCw size={16} className="animate-spin" />
              Checking YouTube Data API connection...
            </div>
          ) : !keyConnected ? (
            <section className="card min-h-64 items-center justify-center p-8 text-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-violet-500/20 bg-violet-500/10 text-violet-300">
                <KeyRound size={21} />
              </div>
              <h2 className="mt-4 font-heading text-lg font-semibold text-surface-900">Connect YouTube Data API</h2>
              <p className="mt-1 max-w-md text-sm leading-relaxed text-surface-500">
                Add and verify a Google Cloud YouTube Data API key before researching niches.
              </p>
              <Button className="mt-5" onClick={() => setMainView('settings')}>
                <KeyRound />
                Open Settings
              </Button>
            </section>
          ) : (
            <>
              <section className="card gap-0 overflow-hidden">
                <div className="flex items-center justify-between gap-4 border-b border-surface-200 px-5 py-4">
                  <div className="flex items-center gap-3">
                    <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary-500/10 text-primary-300">
                      <Radar size={18} />
                    </div>
                    <div>
                      <h2 className="font-heading text-sm font-semibold text-surface-900">Research a niche</h2>
                      <p className="mt-0.5 text-xs text-surface-500">Start broad, then reuse promising keyword ideas as the next seed.</p>
                    </div>
                  </div>
                  <span className="inline-flex items-center gap-1.5 text-xs font-medium text-emerald-300">
                    <CheckCircle2 size={13} />
                    API connected
                  </span>
                </div>

                <form
                  className="space-y-4 p-5"
                  onSubmit={(event) => {
                    event.preventDefault()
                    void runResearch()
                  }}
                >
                  <div className="flex flex-col gap-2 sm:flex-row">
                    <div className="relative min-w-0 flex-1">
                      <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-surface-500" size={16} />
                      <Input
                        className="h-10 pl-9"
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                        placeholder="e.g. sleep stories, senior fitness, AI productivity"
                        aria-label="Seed niche or keyword"
                        maxLength={160}
                      />
                    </div>
                    <Button className="h-10 shrink-0 px-5" type="submit" disabled={query.trim().length < 2 || searching}>
                      {searching ? <RefreshCw className="animate-spin" /> : <Sparkles />}
                      {searching ? 'Researching...' : 'Research niche'}
                    </Button>
                  </div>

                  <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                    <label className="space-y-1.5 text-xs font-medium text-surface-600">
                      Market
                      <select className="input h-9 text-sm" value={region} onChange={(event) => setRegion(event.target.value)}>
                        {REGION_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                      </select>
                    </label>
                    <label className="space-y-1.5 text-xs font-medium text-surface-600">
                      Language
                      <select className="input h-9 text-sm" value={language} onChange={(event) => setLanguage(event.target.value)}>
                        {LANGUAGE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                      </select>
                    </label>
                    <label className="space-y-1.5 text-xs font-medium text-surface-600">
                      Published
                      <select className="input h-9 text-sm" value={period} onChange={(event) => setPeriod(Number(event.target.value))}>
                        {PERIOD_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                      </select>
                    </label>
                    <label className="space-y-1.5 text-xs font-medium text-surface-600">
                      Video length
                      <select className="input h-9 text-sm" value={duration} onChange={(event) => setDuration(event.target.value as VideoDuration)}>
                        {DURATION_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                      </select>
                    </label>
                  </div>
                </form>
              </section>

              {error && (
                <div className="flex items-start gap-2 rounded-xl border border-rose-500/20 bg-rose-500/10 p-4 text-sm text-rose-300">
                  <ShieldAlert size={16} className="mt-0.5 shrink-0" />
                  {error}
                </div>
              )}

              {result && (
                <>
                  <ResultsOverview summary={result.summary} />

                  <div className="grid gap-4 xl:grid-cols-[0.85fr_1.5fr]">
                    <section className="card gap-0 overflow-hidden">
                      <div className="border-b border-surface-200 px-5 py-4">
                        <div className="flex items-center gap-2">
                          <Lightbulb size={16} className="text-amber-300" />
                          <h2 className="font-heading text-sm font-semibold text-surface-900">Keyword ideas</h2>
                        </div>
                        <p className="mt-1 text-xs text-surface-500">Repeated phrases mined from winning video titles.</p>
                      </div>
                      <div className="divide-y divide-surface-200">
                        {result.keyword_ideas.map((idea) => (
                          <button
                            key={idea.keyword}
                            type="button"
                            className="flex w-full items-center justify-between gap-3 px-5 py-3 text-left transition-colors hover:bg-surface-100"
                            onClick={() => setQuery(idea.keyword)}
                          >
                            <span className="min-w-0">
                              <span className="block truncate text-sm font-medium text-surface-900">{idea.keyword}</span>
                              <span className="mt-0.5 block text-xs text-surface-500">{idea.occurrences} title matches</span>
                            </span>
                            <span className="shrink-0 text-right">
                              <span className="block text-xs font-semibold text-surface-800">{formatCompact(idea.avg_views)} views</span>
                              <span className="mt-0.5 block text-[11px] text-surface-500">{formatCompact(idea.avg_views_per_day)}/day</span>
                            </span>
                          </button>
                        ))}
                        {result.keyword_ideas.length === 0 && (
                          <p className="px-5 py-8 text-center text-sm text-surface-500">
                            No repeated title phrases found. Try a broader seed.
                          </p>
                        )}
                      </div>
                    </section>

                    <section className="card gap-0 overflow-hidden">
                      <div className="border-b border-surface-200 px-5 py-4">
                        <div className="flex items-center justify-between gap-3">
                          <div>
                            <h2 className="font-heading text-sm font-semibold text-surface-900">Leading videos</h2>
                            <p className="mt-1 text-xs text-surface-500">Ranked by current views-per-day velocity.</p>
                          </div>
                          <Badge variant="outline">{result.videos.length} results</Badge>
                        </div>
                      </div>
                      <div className="divide-y divide-surface-200">
                        {result.videos.slice(0, 12).map((video) => (
                          <a
                            key={video.video_id}
                            className="group flex items-center gap-3 px-4 py-3 transition-colors hover:bg-surface-100"
                            href={`https://www.youtube.com/watch?v=${encodeURIComponent(video.video_id)}`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            {video.thumbnail_url ? (
                              <img className="h-12 w-20 shrink-0 rounded-md object-cover" src={video.thumbnail_url} alt="" />
                            ) : (
                              <div className="flex h-12 w-20 shrink-0 items-center justify-center rounded-md bg-surface-100 text-surface-500">
                                <Radar size={16} />
                              </div>
                            )}
                            <span className="min-w-0 flex-1">
                              <span className="line-clamp-2 text-sm font-medium leading-snug text-surface-900 group-hover:text-primary-300">{video.title}</span>
                              <span className="mt-1 block truncate text-xs text-surface-500">
                                {video.channel_title} · {formatDate(video.published_at)}
                              </span>
                            </span>
                            <span className="hidden shrink-0 text-right sm:block">
                              <span className="block text-xs font-semibold text-surface-800">{formatCompact(video.views_per_day)}/day</span>
                              <span className="mt-0.5 block text-[11px] text-surface-500">{formatCompact(video.views)} views</span>
                            </span>
                            <ArrowUpRight size={14} className="shrink-0 text-surface-500 group-hover:text-primary-300" />
                          </a>
                        ))}
                        {result.videos.length === 0 && (
                          <p className="px-5 py-10 text-center text-sm text-surface-500">
                            No videos matched these filters. Try a longer period or broader seed.
                          </p>
                        )}
                      </div>
                    </section>
                  </div>

                  <div className="rounded-xl border border-surface-200 bg-surface-0 px-4 py-3 text-xs leading-relaxed text-surface-500">
                    <strong className="font-semibold text-surface-700">How to read this:</strong>{' '}
                    prioritize topics with strong median views and velocity, lower competition, and repeatable breakout by channels under 100K subscribers. This is a directional opportunity model, not a guarantee of performance.
                  </div>
                </>
              )}

              {!result && !error && (
                <section className="grid gap-3 md:grid-cols-3">
                  {[
                    ['1', 'Start with a seed', 'Enter a broad niche, audience problem, or content angle.'],
                    ['2', 'Read the gap', 'Compare demand, competition, velocity, and small-channel breakout.'],
                    ['3', 'Drill down', 'Click a keyword idea and run another search to find a tighter angle.'],
                  ].map(([step, title, copy]) => (
                    <div key={step} className="card gap-0 p-4">
                      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary-500/10 text-xs font-semibold text-primary-300">{step}</span>
                      <h3 className="mt-3 text-sm font-semibold text-surface-900">{title}</h3>
                      <p className="mt-1 text-xs leading-relaxed text-surface-500">{copy}</p>
                    </div>
                  ))}
                </section>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
