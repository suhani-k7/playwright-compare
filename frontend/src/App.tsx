import { useState, useRef, useEffect } from 'react';
import type { ReactElement } from 'react';
import axios from 'axios';
import { postCompare, getStatus, getResults } from "./api";

const BACKEND_URL = 'http://localhost:8000';

interface BBox { x: number; y: number; width: number; height: number; }

interface Annotation {
  tag?: string;
  text?: string;
  alt?: string;
  href?: string;
  issue_type?: string;
  bbox?: BBox;
  extra?: Record<string, unknown>;
}

interface ComparisonResult {
  run_id: string;
  reference_url: string;
  live_url: string;
  results: Record<string, unknown>;
  screenshots: Record<string, { reference: string; live: string; annotated?: string | null }>;
  annotations: Record<string, Annotation[]>;
}

const CATEGORIES = ['headings', 'images', 'buttons', 'links', 'sticky', 'popup', 'metadata'];
const CORE_CATEGORIES = ['headings', 'images', 'buttons', 'links', 'metadata'];
const ALL_STANDARD_CATEGORIES = CORE_CATEGORIES;

interface CategoryMeta {
  label: string;
  description: string;
  icon: (className?: string) => ReactElement;
}

const CATEGORY_META: Record<string, CategoryMeta> = {
  headings: {
    label: 'Headings',
    description: 'Titles & headings (H1–H6)',
    icon: (c) => (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={c}>
        <path d="M6 4v16M6 12h12M18 4v16" />
      </svg>
    ),
  },
  images: {
    label: 'Images',
    description: 'img tags, alt text & placement',
    icon: (c) => (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={c}>
        <rect x="3" y="3" width="18" height="18" rx="2" />
        <circle cx="8.5" cy="8.5" r="1.5" />
        <path d="m21 15-5-5L5 21" />
      </svg>
    ),
  },
  buttons: {
    label: 'Buttons',
    description: 'Buttons & call-to-actions',
    icon: (c) => (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={c}>
        <rect x="2" y="7" width="20" height="10" rx="5" />
        <circle cx="12" cy="12" r="1.5" fill="currentColor" />
      </svg>
    ),
  },
  links: {
    label: 'Links',
    description: 'Anchors & href targets',
    icon: (c) => (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={c}>
        <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
        <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
      </svg>
    ),
  },
  sticky: {
    label: 'Sticky',
    description: 'Sticky / scroll-locked elements',
    icon: (c) => (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={c}>
        <path d="M12 3v5M12 16v5" />
        <rect x="4" y="8" width="16" height="8" rx="2" />
      </svg>
    ),
  },
  popup: {
    label: 'Popup',
    description: 'Modals, popups & overlays',
    icon: (c) => (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={c}>
        <rect x="3" y="3" width="18" height="18" rx="2" />
        <path d="M8 8h8v8H8z" />
      </svg>
    ),
  },
  metadata: {
    label: 'Metadata',
    description: 'Title, description & OG tags',
    icon: (c) => (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={c}>
        <path d="M4 6h16M4 12h16M4 18h10" />
      </svg>
    ),
  },
};

// ─── Small presentational helpers ─────────────────────────────────────────────

function Icon({ path, className }: { path: string; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d={path} />
    </svg>
  );
}

function Spinner({ className = 'h-5 w-5' }: { className?: string }) {
  return (
    <svg className={`animate-spin ${className}`} viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4z" />
    </svg>
  );
}

// ─── Annotation Overlay ───────────────────────────────────────────────────────
const ISSUE_COLOR: Record<string, string> = {
  missing: '#ef4444', extra: '#f59e0b', modified: '#6c63ff',
  mismatch: '#6c63ff', fail: '#ef4444',
};
function issueColor(t?: string) {
  return t ? (ISSUE_COLOR[t.toLowerCase()] ?? '#6c63ff') : '#6c63ff';
}

function AnnotationOverlay({
  imageUrl, annotations, onSelect, selected,
}: {
  imageUrl: string;
  annotations: Annotation[];
  onSelect: (a: Annotation) => void;
  selected: Annotation | null;
}) {
  const imgRef = useRef<HTMLImageElement>(null);
  const [scale, setScale] = useState({ x: 1, y: 1 });

  useEffect(() => {
    function recalc() {
      const img = imgRef.current;
      if (!img || !img.naturalWidth) return;
      setScale({ x: img.clientWidth / img.naturalWidth, y: img.clientHeight / img.naturalHeight });
    }
    const img = imgRef.current;
    if (!img) return;
    img.addEventListener('load', recalc);
    window.addEventListener('resize', recalc);
    recalc();
    return () => { img.removeEventListener('load', recalc); window.removeEventListener('resize', recalc); };
  }, [imageUrl]);

  const valid = annotations.filter(a => a.bbox && a.bbox.width > 0 && a.bbox.height > 0);

  return (
    <div style={{ position: 'relative', display: 'inline-block', width: '100%' }}>
      <img ref={imgRef} src={imageUrl} alt="Annotated" className="block w-full rounded-lg" style={{ display: 'block', width: '100%' }} />
      {valid.map((ann, i) => {
        const { x, y, width, height } = ann.bbox!;
        const color = issueColor(ann.issue_type);
        const isSel = selected === ann;
        return (
          <div
            key={i}
            onClick={() => onSelect(ann)}
            title={ann.text ?? ann.issue_type ?? ''}
            style={{
              position: 'absolute',
              left: x * scale.x, top: y * scale.y,
              width: width * scale.x, height: height * scale.y,
              border: `2px solid ${color}`,
              borderRadius: 3,
              background: isSel ? `${color}33` : `${color}11`,
              boxShadow: isSel ? `0 0 0 3px ${color}55` : 'none',
              cursor: 'pointer',
              zIndex: 10,
              transition: 'background 0.1s',
            }}
            className="hover:bg-black/10"
          />
        );
      })}
    </div>
  );
}

// ─── Detail Panel ─────────────────────────────────────────────────────────────
function DetailPanel({ ann, onClose }: { ann: Annotation | null; onClose: () => void }) {
  if (!ann) return (
    <div className="flex h-full min-h-[180px] items-center justify-center rounded-lg border border-dashed border-slate-700 p-4 text-center text-sm italic text-slate-500">
      Click an annotation box on the annotated image to inspect it.
    </div>
  );

  const color = issueColor(ann.issue_type);
  const bbox = ann.bbox;

  return (
    <div className="overflow-y-auto rounded-lg border border-slate-800 bg-slate-900/60 p-4 text-sm" style={{ maxHeight: 480 }}>
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          {ann.issue_type && (
            <span className="rounded px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider"
              style={{ background: color + '22', color, border: `1px solid ${color}` }}>
              {ann.issue_type}
            </span>
          )}
          {ann.tag && <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-xs text-slate-400">&lt;{ann.tag}&gt;</span>}
        </div>
        <button onClick={onClose}
          className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-400 transition-colors hover:border-slate-500 hover:text-white">
          ✕
        </button>
      </div>

      {ann.text && <Row label="Text" value={ann.text} />}
      {ann.alt && <Row label="Alt" value={ann.alt} />}
      {ann.href && <Row label="Href" value={ann.href} />}
      {ann.tag && <Row label="Tag" value={ann.tag} />}

      {bbox && (
        <div className="mt-3">
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">Bounding Box</p>
          <div className="grid grid-cols-2 gap-1">
            {(['x', 'y', 'width', 'height'] as const).map(k => (
              <div key={k} className="flex justify-between rounded bg-slate-800/80 px-2 py-1">
                <span className="font-mono text-xs text-slate-500">{k}</span>
                <span className="font-mono text-xs text-slate-200">{Math.round(bbox[k])}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {ann.extra && Object.keys(ann.extra).length > 0 && (
        <div className="mt-3">
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">Additional</p>
          {Object.entries(ann.extra).map(([k, v]) => <Row key={k} label={k} value={String(v)} />)}
        </div>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="mt-2">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{label}</p>
      <p className="mt-0.5 break-all rounded bg-slate-800/80 px-2 py-1 font-mono text-xs text-slate-200">{value}</p>
    </div>
  );
}

// ─── Landing page ─────────────────────────────────────────────────────────────
function Landing({
  refUrl, liveUrl, selected, allAnnotations, loading, message, isError,
  onRefChange, onLiveChange, onToggleCategory, onToggleAll, onStart,
}: {
  refUrl: string;
  liveUrl: string;
  selected: string[];
  allAnnotations: boolean;
  loading: boolean;
  message: string;
  isError: boolean;
  onRefChange: (v: string) => void;
  onLiveChange: (v: string) => void;
  onToggleCategory: (cat: string) => void;
  onToggleAll: (v: boolean) => void;
  onStart: () => void;
}) {
  const canSubmit = !loading && refUrl && liveUrl;

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-10 sm:px-6 sm:py-14">
      {/* Hero */}
      <div className="mb-10 text-center">
        <div className="mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-500 to-indigo-600 shadow-lg shadow-brand-500/30">
          <svg viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-8 w-8">
            <rect x="2" y="3" width="9" height="13" rx="1.5" />
            <rect x="13" y="8" width="9" height="13" rx="1.5" />
            <path d="M6.5 17v4M17.5 8V4" />
          </svg>
        </div>
        <h1 className="text-3xl font-bold tracking-tight text-white sm:text-4xl">
          Visual Comparison Studio
        </h1>
        <p className="mx-auto mt-3 max-w-xl text-sm leading-relaxed text-slate-400 sm:text-base">
          Capture a reference and live version of any page, then highlight every visual difference
          across headings, images, buttons, links and more.
        </p>
      </div>

      <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 shadow-2xl shadow-black/40 backdrop-blur sm:p-8">
        {/* URLs */}
        <div className="grid gap-5 sm:grid-cols-2">
          <div>
            <label htmlFor="ref-url" className="mb-1.5 flex items-center gap-2 text-sm font-medium text-slate-200">
              <span className="flex h-5 w-5 items-center justify-center rounded bg-emerald-500/15 text-emerald-400">
                <Icon path="M4 12h16M4 12l4-4M4 12l4 4" className="h-3.5 w-3.5" />
              </span>
              Reference URL
            </label>
            <input
              id="ref-url"
              type="url"
              value={refUrl}
              onChange={e => onRefChange(e.target.value)}
              placeholder="https://your-site.example.com/page"
              disabled={loading}
              className="w-full rounded-lg border border-slate-700 bg-slate-950/70 px-3.5 py-2.5 text-sm text-white placeholder:text-slate-600 transition-colors focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/30 disabled:opacity-50"
            />
          </div>
          <div>
            <label htmlFor="live-url" className="mb-1.5 flex items-center gap-2 text-sm font-medium text-slate-200">
              <span className="flex h-5 w-5 items-center justify-center rounded bg-sky-500/15 text-sky-400">
                <Icon path="M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM12 3a15.3 15.3 0 0 1 4 9 15.3 15.3 0 0 1-4 9 15.3 15.3 0 0 1-4-9 15.3 15.3 0 0 1 4-9z" className="h-3.5 w-3.5" />
              </span>
              Live URL
            </label>
            <input
              id="live-url"
              type="url"
              value={liveUrl}
              onChange={e => onLiveChange(e.target.value)}
              placeholder="https://your-site.example.com/page"
              disabled={loading}
              className="w-full rounded-lg border border-slate-700 bg-slate-950/70 px-3.5 py-2.5 text-sm text-white placeholder:text-slate-600 transition-colors focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/30 disabled:opacity-50"
            />
          </div>
        </div>

        {/* Categories */}
        <div className="mt-7">
          <div className="mb-3 flex items-center justify-between">
            <p className="text-sm font-medium text-slate-200">Categories to compare</p>
            <span className="text-xs text-slate-500">{selected.length} selected</span>
          </div>
          <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3">
            {CATEGORIES.map(cat => {
              const meta = CATEGORY_META[cat];
              const isOn = selected.includes(cat);
              const dimmed = allAnnotations && !CORE_CATEGORIES.includes(cat);
              return (
                <button
                  key={cat}
                  type="button"
                  onClick={() => onToggleCategory(cat)}
                  disabled={loading}
                  aria-pressed={isOn}
                  title={dimmed ? 'Ignored when comparing all grounds' : undefined}
                  className={[
                    'group flex items-start gap-2.5 rounded-xl border p-3 text-left transition-all',
                    isOn
                      ? 'border-brand-500 bg-brand-500/10 shadow-sm shadow-brand-500/10'
                      : 'border-slate-800 bg-slate-950/40 hover:border-slate-600',
                    dimmed ? 'opacity-40' : '',
                    loading ? 'cursor-not-allowed' : 'cursor-pointer',
                  ].join(' ')}
                >
                  <span className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-colors ${isOn ? 'bg-brand-500 text-white' : 'bg-slate-800 text-slate-400 group-hover:text-slate-200'}`}>
                    {meta.icon('h-4 w-4')}
                  </span>
                  <span className="min-w-0">
                    <span className="block text-sm font-medium text-slate-100">{meta.label}</span>
                    <span className="block truncate text-[11px] text-slate-500">{meta.description}</span>
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {/* All grounds toggle */}
        <label className="mt-6 flex cursor-pointer items-start gap-3 rounded-xl border border-slate-800 bg-slate-950/40 p-4 transition-colors hover:border-slate-600">
          <input
            type="checkbox"
            checked={allAnnotations}
            onChange={e => onToggleAll(e.target.checked)}
            disabled={loading}
            className="mt-0.5 h-4 w-4 rounded border-slate-600 text-brand-500 accent-brand-500 focus:ring-brand-500"
          />
          <span>
            <span className="block text-sm font-medium text-slate-100">Compare All Grounds</span>
            <span className="block text-xs text-slate-500">
              General page comparison across headings, images, buttons, links and metadata.
            </span>
          </span>
        </label>

        {/* Status + submit */}
        {message && (
          <div className={`mt-6 flex items-center gap-2.5 rounded-lg border px-3.5 py-2.5 text-sm ${isError ? 'border-red-500/40 bg-red-500/10 text-red-300' : 'border-slate-700 bg-slate-800/60 text-slate-200'}`}>
            {loading && !isError ? <Spinner className="h-4 w-4 shrink-0 text-brand-400" /> : (
              <Icon path={isError ? 'M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z' : 'M5 12l5 5 9-10'} className="h-4 w-4 shrink-0" />
            )}
            <span>{message}</span>
          </div>
        )}

        <button
          onClick={onStart}
          disabled={!canSubmit}
          className="mt-6 flex w-full items-center justify-center gap-2.5 rounded-xl bg-gradient-to-r from-brand-500 to-indigo-600 px-6 py-3.5 text-sm font-semibold text-white shadow-lg shadow-brand-500/25 transition-all hover:brightness-110 active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:brightness-100"
        >
          {loading ? (
            <>
              <Spinner className="h-5 w-5" />
              Running comparison…
            </>
          ) : (
            <>
              <Icon path="M17 3h4v4M4 17V4h7M17 7 3 21" className="h-4.5 w-4.5" />
              Run Comparison
            </>
          )}
        </button>
      </div>
    </div>
  );
}

// ─── Results page ─────────────────────────────────────────────────────────────
function Results({
  result, onBack,
}: {
  result: ComparisonResult;
  onBack: () => void;
}) {
  const screenshotsToShow: Record<string, { reference: string; live: string; annotated?: string | null }> = {};
  let coreImgs: { reference: string; live: string; annotated?: string | null } | null = null;
  let hasCore = false;

  Object.entries(result.screenshots).forEach(([cat, imgs]) => {
    if (CORE_CATEGORIES.includes(cat)) {
      hasCore = true;
      if (!coreImgs || (!coreImgs.annotated && imgs.annotated)) coreImgs = imgs;
    } else {
      screenshotsToShow[cat] = imgs;
    }
  });
  if (hasCore && coreImgs) screenshotsToShow['General Comparison'] = coreImgs;

  const annotationsToShow: Record<string, Annotation[]> = {};
  if (result.annotations) {
    Object.entries(result.annotations).forEach(([cat, anns]) => {
      if (CORE_CATEGORIES.includes(cat)) {
        annotationsToShow['General Comparison'] = [
          ...(annotationsToShow['General Comparison'] ?? []),
          ...anns,
        ];
      } else {
        annotationsToShow[cat] = anns;
      }
    });
  }

  const [activeAnn, setActiveAnn] = useState<Annotation | null>(null);

  const totalAnns = Object.values(annotationsToShow).reduce((n, arr) => n + arr.length, 0);

  return (
    <div className="min-h-screen pb-16">
      {/* Top bar */}
      <header className="sticky top-0 z-20 border-b border-slate-800/80 bg-slate-950/85 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3.5 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-brand-500 to-indigo-600">
              <svg viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
                <rect x="2" y="3" width="9" height="13" rx="1.5" />
                <rect x="13" y="8" width="9" height="13" rx="1.5" />
                <path d="M6.5 17v4M17.5 8V4" />
              </svg>
            </div>
            <div className="min-w-0">
              <h2 className="truncate text-base font-semibold text-white sm:text-lg">Comparison Results</h2>
              <p className="truncate text-xs text-slate-500">
                {result.reference_url} <span className="text-slate-600">→</span> {result.live_url}
              </p>
            </div>
          </div>
          <button
            onClick={onBack}
            className="flex shrink-0 items-center gap-2 rounded-lg border border-slate-700 bg-slate-800/70 px-3.5 py-2 text-sm font-medium text-slate-200 transition-colors hover:border-slate-500 hover:text-white"
          >
            <Icon path="M3 12h18M7 8l-4 4 4 4" className="h-4 w-4" />
            New Comparison
          </button>
        </div>
      </header>

      <div className="mx-auto max-w-7xl px-4 pt-6 sm:px-6">
        {/* Summary strip */}
        <div className="mb-8 flex flex-wrap items-center gap-2">
          <span className="mr-1 inline-flex items-center gap-2 rounded-full border border-slate-700 bg-slate-800/70 px-3.5 py-1.5 text-xs font-medium text-slate-200">
            <span className="inline-block h-2 w-2 rounded-full bg-brand-500" />
            Run {result.run_id.slice(0, 8)}
          </span>
          {Object.entries(annotationsToShow).map(([cat, anns]) => (
            <span key={cat}
              className="inline-flex items-center gap-1.5 rounded-full border border-slate-800 bg-slate-900/80 px-3 py-1.5 text-xs text-slate-300">
              <span className="capitalize">{cat === 'General Comparison' ? 'General' : cat}</span>
              <span className={anns.length > 0 ? 'font-semibold text-amber-400' : 'font-semibold text-slate-600'}>
                {anns.length}
              </span>
            </span>
          ))}
          {totalAnns > 0 && (
            <span className="ml-auto rounded-full bg-amber-500/10 px-3.5 py-1.5 text-xs font-medium text-amber-300">
              {totalAnns} difference{totalAnns !== 1 ? 's' : ''} found
            </span>
          )}
        </div>

        {Object.entries(screenshotsToShow).map(([cat, imgs]) => {
          const anns: Annotation[] = annotationsToShow[cat] ?? [];
          const hasAnns = anns.some(a => a.bbox && a.bbox.width > 0);
          const meta = CATEGORY_META[cat === 'General Comparison' ? 'headings' : cat];

          return (
            <section key={cat} className="mb-8 overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/50">
              <div className="flex flex-wrap items-center gap-3 border-b border-slate-800 bg-slate-900/70 px-5 py-4">
                <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-800 text-brand-400">
                  {meta ? meta.icon('h-4.5 w-4.5') : <Icon path="M4 6h16M4 12h16M4 18h10" className="h-4 w-4" />}
                </span>
                <div className="min-w-0 flex-1">
                  <h3 className="text-base font-semibold capitalize text-white">
                    {cat === 'General Comparison' ? 'General Comparison' : meta?.label ?? cat}
                  </h3>
                  <p className="text-xs text-slate-500">
                    {cat === 'General Comparison' ? 'Combined core categories' : meta?.description}
                  </p>
                </div>
                {anns.length > 0 && (
                  <span className="rounded-full px-3 py-1 text-xs font-semibold"
                    style={{ background: '#6c63ff22', color: '#a5a0ff', border: '1px solid #6c63ff55' }}>
                    {anns.length} annotation{anns.length !== 1 ? 's' : ''}
                  </span>
                )}
              </div>

              <div className="p-5">
                <div className="grid gap-5" style={{ gridTemplateColumns: hasAnns ? '1fr 1fr 1fr 300px' : '1fr 1fr 1fr' }}>
                  {/* Reference */}
                  <div>
                    <p className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-slate-200">
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                      Reference
                    </p>
                    {imgs.reference
                      ? <img src={`${BACKEND_URL}${imgs.reference}`} alt="reference" className="max-w-full rounded-lg border border-slate-800" />
                      : <p className="text-sm italic text-slate-500">No reference screenshot</p>}
                  </div>

                  {/* Live */}
                  <div>
                    <p className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-slate-200">
                      <span className="h-1.5 w-1.5 rounded-full bg-sky-400" />
                      Live
                    </p>
                    {imgs.live
                      ? <img src={`${BACKEND_URL}${imgs.live}`} alt="live" className="max-w-full rounded-lg border border-slate-800" />
                      : <p className="text-sm italic text-slate-500">No live screenshot</p>}
                  </div>

                  {/* Annotated — with clickable overlays if bbox data exists */}
                  <div>
                    <p className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-slate-200">
                      <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
                      Annotated
                      {hasAnns && <span className="font-normal text-slate-500">(click to inspect)</span>}
                    </p>
                    {imgs.annotated ? (
                      hasAnns ? (
                        <AnnotationOverlay
                          imageUrl={`${BACKEND_URL}${imgs.annotated}`}
                          annotations={anns}
                          onSelect={a => setActiveAnn(prev => prev === a ? null : a)}
                          selected={activeAnn}
                        />
                      ) : (
                        <img src={`${BACKEND_URL}${imgs.annotated}`} alt="annotated" className="max-w-full rounded-lg border border-slate-800" />
                      )
                    ) : (
                      <p className="text-sm italic text-slate-500">No annotated screenshot</p>
                    )}
                  </div>

                  {/* Detail panel — only shown when annotations exist */}
                  {hasAnns && (
                    <div>
                      <p className="mb-2 text-sm font-semibold text-slate-200">Details</p>
                      <DetailPanel ann={activeAnn} onClose={() => setActiveAnn(null)} />
                    </div>
                  )}
                </div>
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}

// ─── Main App ────────────────────────────────────────────────────────────────
export default function App() {
  const [refUrl, setRefUrl] = useState('');
  const [liveUrl, setLiveUrl] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [result, setResult] = useState<ComparisonResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [isError, setIsError] = useState(false);
  const [allAnnotations, setAllAnnotations] = useState(false);

  const toggleCategory = (cat: string) =>
    setSelected(prev => prev.includes(cat) ? prev.filter(c => c !== cat) : [...prev, cat]);

  const startComparison = async () => {
    if (!refUrl || !liveUrl || (selected.length === 0 && !allAnnotations)) {
      setMessage('Please fill URLs and select at least one category.');
      setIsError(true);
      return;
    }
    setLoading(true);
    setIsError(false);
    setMessage('Submitting comparison…');
    try {
      const categoriesToSend = allAnnotations ? ALL_STANDARD_CATEGORIES : selected;

      const compareResp = await postCompare({
        reference_url: refUrl,
        live_url: liveUrl,
        categories: categoriesToSend,
        all_annotations: allAnnotations,
      });
      const id = compareResp.data.run_id;
      setMessage('Comparison started, polling status…');
      let status = 'pending';
      while (status !== 'done' && status !== 'failed') {
        await new Promise(r => setTimeout(r, 3000));
        const statusResp = await getStatus(id);
        status = statusResp.data.status;
        setMessage(status === 'running' ? 'Running… this may take 1–3 min' : 'Pending…');
      }
      if (status === 'failed') {
        setMessage('Comparison failed.');
        setIsError(true);
        setLoading(false);
        return;
      }
      const resultResp = await getResults(id);
      setResult(resultResp.data);
      setMessage('');
      setIsError(false);
    } catch (err) {
      console.error(err);
      setMessage('Error occurred during comparison.');
      setIsError(true);
    } finally {
      setLoading(false);
    }
  };

  if (result) {
    return (
      <Results
        result={result}
        onBack={() => { setResult(null); }}
      />
    );
  }

  return (
    <Landing
      refUrl={refUrl}
      liveUrl={liveUrl}
      selected={selected}
      allAnnotations={allAnnotations}
      loading={loading}
      message={message}
      isError={isError}
      onRefChange={setRefUrl}
      onLiveChange={setLiveUrl}
      onToggleCategory={toggleCategory}
      onToggleAll={setAllAnnotations}
      onStart={startComparison}
    />
  );
}
