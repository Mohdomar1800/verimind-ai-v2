import React, { useState, useEffect } from 'react';
import { getCoveragePriorities, getExplanation } from '../services/api';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from 'recharts';

const REPO_COLORS = {
  opentitan: '#FB5E4C',
  ibex: '#F7CC66',
  cva6: '#98E4C1',
  veer_eh1: '#BE8EF8',
};

function CoveragePriority({ runId, featureRunId, onBack }) {
  const [priorities, setPriorities] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedItem, setSelectedItem] = useState(null);
  const [explanation, setExplanation] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchData();
  }, [runId, featureRunId]);

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getCoveragePriorities(runId, featureRunId);
      const mapped = (data.items || []).map(item => ({
        ...item,
        // Backend returns item_id like 'repo:module' often
        module_name: item.evidence?.module_name || (item.item_id.includes(':') ? item.item_id.split(':')[1] : item.item_id),
        repo: item.evidence?.repo || (item.item_id.includes(':') ? item.item_id.split(':')[0] : 'unknown'),
        coverage_gap: item.evidence?.coverage_gap || item.evidence?.gap || 40,
      }));
      setPriorities(mapped);
    } catch (error) {
      console.error('Failed to fetch coverage priorities:', error);
      setError('Backend is down or data unavailable.');
    }
    setLoading(false);
  };



  const handleRowClick = async (item) => {
    setSelectedItem(item);
    try {
      const exp = await getExplanation(item.item_id);
      setExplanation(exp);
    } catch (error) {
      console.error('Failed to fetch explanation:', error);
      setExplanation({
        reasons: [
          `Failed to fetch real explanation from backend.`,
        ],
        evidence: {
          error: "Backend is down or data unavailable"
        },
      });
    }
  };

  // Repo distribution for pie chart
  const repoDistribution = priorities.reduce((acc, p) => {
    acc[p.repo] = (acc[p.repo] || 0) + 1;
    return acc;
  }, {});

  const pieData = Object.entries(repoDistribution).map(([repo, count]) => ({
    name: repo,
    value: count,
    color: REPO_COLORS[repo] || '#BE8EF8',
  }));

  const barData = priorities.slice(0, 10).map(p => ({
    name: (p.module_name || '').slice(0, 12),
    score: (p.score || 0) * 100,
    gap: p.coverage_gap || 40,
  }));

  if (loading) {
    return (
      <div className="main-container">
        <div className="loading">Analyzing coverage priorities...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="main-container">
        <div className="page-header">
          <h1 className="page-title">Coverage Priority</h1>
          <button className="back-btn" onClick={onBack}>← Go Back</button>
        </div>
        <div style={{ padding: '20px', background: '#ffebee', color: '#c62828', borderRadius: '8px', border: '1px solid #ef5350', marginTop: '20px' }}>
          <h3>System Error</h3>
          <p>{error}</p>
          <button className="btn btn-primary" style={{ marginTop: '10px' }} onClick={fetchData}>Retry Data Fetch</button>
        </div>
      </div>
    );
  }

  return (
    <div className="main-container">
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h1 className="page-title">Coverage Priority Ranking</h1>
          <p style={{ color: '#444', fontSize: '13px', marginTop: '-5px' }}>
            Context: <span style={{ fontWeight: '600' }}>{runId}</span>
          </p>
        </div>
        <div style={{ display: 'flex', gap: '10px' }}>
          <button className="btn btn-secondary" onClick={fetchData}>Refresh Ranking</button>
          <button className="back-btn" onClick={onBack}>← Go Back</button>
        </div>
      </div>

      <p style={{ marginBottom: '20px', fontSize: '16px' }}>
        AI-enabled functional coverage prioritization to focus verification effort on the most impactful coverage points.
      </p>

      {/* Stats */}
      <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
        <div className="stat-card">
          <div className="stat-value">{priorities.length}</div>
          <div className="stat-label">Coverage Points</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{priorities.filter(p => p.score >= 0.8).length}</div>
          <div className="stat-label">High Priority</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">
            {priorities.length > 0
              ? Math.round(priorities.reduce((sum, p) => sum + (p.coverage_gap || 40), 0) / priorities.length)
              : 0}%
          </div>
          <div className="stat-label">Avg Coverage Gap</div>
        </div>
      </div>

      <div className="two-columns">
        {/* Repo Distribution */}
        <div className="chart-container">
          <h3 className="chart-title">Repository Distribution</h3>
          <ResponsiveContainer width="100%" height={250}>
            <PieChart>
              <Pie
                data={pieData}
                cx="50%"
                cy="50%"
                outerRadius={80}
                dataKey="value"
                label={({ name, value }) => `${name}: ${value}`}
              >
                {pieData.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={entry.color} stroke="black" strokeWidth={1} />
                ))}
              </Pie>
              <Tooltip />
            </PieChart>
          </ResponsiveContainer>
        </div>

        {/* Priority Scores */}
        <div className="chart-container">
          <h3 className="chart-title">Priority Scores</h3>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={barData}>
              <XAxis dataKey="name" tick={{ fontSize: 11 }} angle={-45} textAnchor="end" height={60} />
              <YAxis domain={[0, 100]} />
              <Tooltip />
              <Bar dataKey="score" fill="#F7CC66" stroke="black" strokeWidth={1} name="Priority Score" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Data Table */}
      <div className="data-table-container" style={{ marginTop: '20px' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>Rank</th>
              <th>Module</th>
              <th>Repository</th>
              <th>Priority Score</th>
              <th>Coverage Gap</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {priorities.map((item, idx) => (
              <tr
                key={idx}
                onClick={() => handleRowClick(item)}
                style={{ cursor: 'pointer', background: selectedItem?.item_id === item.item_id ? '#FEE3D2' : '' }}
              >
                <td><strong>#{item.rank || idx + 1}</strong></td>
                <td>{item.module_name}</td>
                <td>
                  <span style={{
                    padding: '2px 8px',
                    borderRadius: '10px',
                    background: REPO_COLORS[item.repo] || '#ddd',
                    fontSize: '12px'
                  }}>
                    {item.repo}
                  </span>
                </td>
                <td>
                  <div className="progress-bar" style={{ width: '100px', height: '16px' }}>
                    <div className="progress-fill" style={{ width: `${item.score * 100}%` }}></div>
                  </div>
                  <span style={{ marginLeft: '8px' }}>{(item.score * 100).toFixed(0)}%</span>
                </td>
                <td>{item.coverage_gap || '—'}%</td>
                <td>
                  <button
                    className="btn btn-primary"
                    style={{ padding: '5px 12px', fontSize: '12px' }}
                    onClick={(e) => { e.stopPropagation(); handleRowClick(item); }}
                  >
                    View
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Explanation Panel */}
      {selectedItem && (
        <div className="chart-container" style={{ marginTop: '20px' }}>
          <h3 className="chart-title">
            Evidence: {selectedItem.repo}:{selectedItem.module_name}
          </h3>
          <div className="evidence-panel">
            {explanation && (
              <>
                <div style={{ marginBottom: '15px' }}>
                  <strong>Why prioritize this coverage point?</strong>
                  <ul style={{ marginTop: '10px', paddingLeft: '20px' }}>
                    {explanation.reasons?.map((r, i) => (
                      <li key={i} style={{ marginBottom: '5px' }}>{r}</li>
                    ))}
                  </ul>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '15px' }}>
                  {Object.entries(explanation.evidence || {}).map(([key, val]) => (
                    <div key={key} style={{ background: 'white', padding: '15px', borderRadius: '5px', border: '1px solid black' }}>
                      <div style={{ fontSize: '12px', color: '#666' }}>{key.replace(/_/g, ' ')}</div>
                      <div style={{ fontSize: '24px', fontWeight: 700 }}>
                        {typeof val === 'number' ? val.toFixed(2) : val}
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* Export */}
      <div style={{ marginTop: '20px', textAlign: 'right' }}>
        <button
          className="btn btn-secondary"
          onClick={() => {
            const csv = priorities.map(p =>
              `${p.rank || ''},${p.repo},${p.module_name},${p.score},${p.coverage_gap || ''}`
            ).join('\n');
            const blob = new Blob([`rank,repo,module,score,coverage_gap\n${csv}`], { type: 'text/csv' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `coverage_priorities_${runId}.csv`;
            a.click();
          }}
        >
          Export CSV
        </button>
      </div>
    </div>
  );
}

export default CoveragePriority;
