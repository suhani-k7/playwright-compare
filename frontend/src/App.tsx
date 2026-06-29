import React, { useState } from 'react';
import axios from 'axios';

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
  const [runId, setRunId] = useState<string>('');
  const [result, setResult] = useState<ComparisonResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');

  const toggleCategory = (cat: string) => {
    setSelected(prev =>
      prev.includes(cat) ? prev.filter(c => c !== cat) : [...prev, cat]
    );
  };

  const startComparison = async () => {
    if (!refUrl || !liveUrl || selected.length === 0) {
      setMessage('Please fill URLs and select at least one category.');
      return;
    }
    setLoading(true);
    setMessage('Submitting comparison...');
    try {
      const compareResp = await axios.post('/compare', {
        reference_url: refUrl,
        live_url: liveUrl,
        categories: selected,
      });
      const id = compareResp.data.run_id;
      setRunId(id);
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
    return (
      <div className="p-4 max-w-4xl mx-auto">
        <h2 className="text-2xl font-semibold mb-4">Comparison Results</h2>
        {Object.entries(result.screenshots).map(([cat, imgs]) => (
          <div key={cat} className="mb-6">
            <h3 className="text-xl font-medium capitalize mb-2">{cat}</h3>
            <div className="grid grid-cols-3 gap-4 items-start">
              <div>
                <p className="text-sm font-semibold mb-1">Reference</p>
                <img src={imgs.reference} alt={`${cat} reference`} className="border rounded max-w-full" />
              </div>
              <div>
                <p className="text-sm font-semibold mb-1">Live</p>
                <img src={imgs.live} alt={`${cat} live`} className="border rounded max-w-full" />
              </div>
              {imgs.annotated && (
                <div>
                  <p className="text-sm font-semibold mb-1">Annotated</p>
                  <img src={imgs.annotated} alt={`${cat} annotated`} className="border rounded max-w-full" />
                </div>
              )}
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