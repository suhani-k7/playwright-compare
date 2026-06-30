import { useState } from 'react';
import axios from 'axios';

// Backend base URL – adjust if needed
const BACKEND_URL = 'http://localhost:8000';

interface ComparisonResult {
  run_id: string;
  reference_url: string;
  live_url: string;
  results: Record<string, any>;
  screenshots: Record<string, { reference: string; live: string; annotated?: string | null }>;
}

const CATEGORIES = [
  'headings',
  'images',
  'buttons',
  'links',
  'sticky',
  'popup',
  'metadata',
];

export default function App() {
  const [refUrl, setRefUrl] = useState('');
  const [liveUrl, setLiveUrl] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [result, setResult] = useState<ComparisonResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [allAnnotations, setAllAnnotations] = useState(false);

  const toggleCategory = (cat: string) => {
    setSelected(prev =>
      prev.includes(cat) ? prev.filter(c => c !== cat) : [...prev, cat]
    );
  };

  const startComparison = async () => {
    if (!refUrl || !liveUrl || (selected.length === 0 && !allAnnotations)) {
      setMessage('Please fill URLs and select at least one category.');
      return;
    }
    setLoading(true);
    setMessage('Submitting comparison...');
    try {
      const ALL_STANDARD_CATEGORIES = [
        "headings",
        "images",
        "buttons",
        "links",
        "metadata",
      ];

      const categoriesToSend = allAnnotations ? ALL_STANDARD_CATEGORIES : selected;

      const compareResp = await axios.post('/compare', {
        reference_url: refUrl,
        live_url: liveUrl,
        categories: categoriesToSend,
        all_annotations: allAnnotations,
      });
      const id = compareResp.data.run_id;
      setMessage('Comparison started, polling status...');
      let status = 'pending';
      while (status !== 'done' && status !== 'failed') {
        // eslint-disable-next-line no-await-in-loop
        const statusResp = await axios.get(`/status/${id}`);
        status = statusResp.data.status;
        // eslint-disable-next-line no-await-in-loop
        await new Promise(r => setTimeout(r, 1000));
      }
      if (status === 'failed') {
        setMessage('Comparison failed.');
        setLoading(false);
        return;
      }
      const resultResp = await axios.get(`/results/${id}`);
      console.log(resultResp.data);
      setResult(resultResp.data);
      setMessage('');
    } catch (err) {
      console.error(err);
      setMessage('Error occurred during comparison.');
    } finally {
      setLoading(false);
    }
  };

  if (result) {
    const CORE_CATEGORIES = ['headings', 'images', 'buttons', 'links', 'metadata'];
    const screenshotsToShow: Record<string, { reference: string; live: string; annotated?: string | null }> = {};
    let hasCore = false;
    let coreImgs: { reference: string; live: string; annotated?: string | null } | null = null;

    Object.entries(result.screenshots).forEach(([cat, imgs]) => {
      if (CORE_CATEGORIES.includes(cat)) {
        hasCore = true;
        if (!coreImgs || (!coreImgs.annotated && imgs.annotated)) {
          coreImgs = imgs;
        }
      } else {
        screenshotsToShow[cat] = imgs;
      }
    });

    if (hasCore && coreImgs) {
      screenshotsToShow['General Comparison'] = coreImgs;
    }

    return (
      <div className="p-4 max-w-4xl mx-auto">
        <div className="flex justify-between items-center mb-6">
          <h2 className="text-2xl font-semibold">Comparison Results</h2>
          <button
            onClick={() => {
              setResult(null);
            }}
            className="bg-gray-600 hover:bg-gray-700 text-white px-4 py-2 rounded transition-colors"
          >
            Run Another Comparison
          </button>
        </div>
        {Object.entries(screenshotsToShow).map(([cat, imgs]) => (
          <div key={cat} className="mb-6">
            <h3 className="text-xl font-medium capitalize mb-2">{cat}</h3>
            <div className="grid grid-cols-3 gap-4 items-start">
              <div>
                <p className="text-sm font-semibold mb-1">Reference</p>
                {imgs.reference ? (
                  <img src={`${BACKEND_URL}${imgs.reference}`} alt={`${cat} reference`} className="border rounded max-w-full" />
                ) : (
                  <p className="text-gray-500 italic">No reference screenshot available</p>
                )}
              </div>
              <div>
                <p className="text-sm font-semibold mb-1">Live</p>
                {imgs.live ? (
                  <img src={`${BACKEND_URL}${imgs.live}`} alt={`${cat} live`} className="border rounded max-w-full" />
                ) : (
                  <p className="text-gray-500 italic">No live screenshot available</p>
                )}
              </div>
              <div>
                <p className="text-sm font-semibold mb-1">Annotated</p>
                {imgs.annotated ? (
                  <img src={`${BACKEND_URL}${imgs.annotated}`} alt={`${cat} annotated`} className="border rounded max-w-full" />
                ) : (
                  <p className="text-gray-500 italic">No annotated screenshot available</p>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="max-w-xl mx-auto p-4">
      <h2 className="text-2xl font-semibold mb-4">Run Website Comparison</h2>
      <div className="space-y-4">
        <div>
          <label className="block mb-1">Reference URL</label>
          <input
            type="url"
            value={refUrl}
            onChange={e => setRefUrl(e.target.value)}
            className="w-full border rounded p-2"
            required
          />
        </div>
        <div>
          <label className="block mb-1">Live URL</label>
          <input
            type="url"
            value={liveUrl}
            onChange={e => setLiveUrl(e.target.value)}
            className="w-full border rounded p-2"
            required
          />
        </div>
        <div>
          <p className="mb-1">Select categories</p>
          <div className="grid grid-cols-2 gap-2">
            {CATEGORIES.map(cat => (
              <label key={cat} className="flex items-center">
                <input
                  type="checkbox"
                  checked={selected.includes(cat)}
                  onChange={() => toggleCategory(cat)}
                  className="mr-2"
                />
                {cat}
              </label>
            ))}
          </div>
        </div>
        <div>
          <label className="flex items-center font-medium select-none cursor-pointer mt-3">
            <input
              type="checkbox"
              checked={allAnnotations}
              onChange={e => setAllAnnotations(e.target.checked)}
              className="mr-2 h-4 w-4 text-blue-600 rounded border-gray-300 focus:ring-blue-500"
            />
            Compare All Grounds (General Page Comparison)
          </label>
        </div>
        {message && <p className="text-sm text-red-600">{message}</p>}
        <button
          onClick={startComparison}
          disabled={loading}
          className="bg-blue-600 text-white px-4 py-2 rounded disabled:opacity-50"
        >
          {loading ? 'Running...' : 'Run Comparison'}
        </button>

      </div>
    </div>
  );
}