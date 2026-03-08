import React, { useState } from 'react';
import './App.css';
import Dashboard from './components/Dashboard';
import FailurePrediction from './components/FailurePrediction';
import CoveragePriority from './components/CoveragePriority';
import DebugQueue from './components/DebugQueue';
import VerificationPlan from './components/VerificationPlan';
import Settings from './components/Settings';

function App() {
  const [currentPage, setCurrentPage] = useState('dashboard');
  const [runId, setRunId] = useState('run_m4_dataset_gen');
  const [featureRunId, setFeatureRunId] = useState('feat_dataset_gen');
  const [refreshKey, setRefreshKey] = useState(0);

  const handlePipelineComplete = () => {
    // Always bump refreshKey so Dashboard re-fetches even if runId didn't change
    setRefreshKey(prev => prev + 1);
    setCurrentPage('dashboard');
  };

  const renderPage = () => {
    switch (currentPage) {
      case 'failure':
        return <FailurePrediction runId={runId} featureRunId={featureRunId} onBack={() => setCurrentPage('dashboard')} />;
      case 'coverage':
        return <CoveragePriority runId={runId} featureRunId={featureRunId} onBack={() => setCurrentPage('dashboard')} />;
      case 'debug':
        return <DebugQueue runId={runId} featureRunId={featureRunId} onBack={() => setCurrentPage('dashboard')} />;
      case 'plan':
        return <VerificationPlan runId={runId} featureRunId={featureRunId} onBack={() => setCurrentPage('dashboard')} />;
      case 'settings':
        return (
          <Settings
            runId={runId}
            setRunId={setRunId}
            featureRunId={featureRunId}
            setFeatureRunId={setFeatureRunId}
            onPipelineComplete={handlePipelineComplete}
          />
        );
      default:
        return (
          <Dashboard
            onNavigate={setCurrentPage}
            runId={runId}
            setRunId={setRunId}
            featureRunId={featureRunId}
            setFeatureRunId={setFeatureRunId}
            refreshKey={refreshKey}
          />
        );
    }
  };

  return (
    <div className="app">
      {/* Navbar */}
      <nav className="navbar">
        <div className="logo">
          <div className="logo-box">
            <div className="logo-line"></div>
            <div className="logo-line"></div>
          </div>
          <div className="logo-text">VeriMind4</div>
        </div>
        <div className="nav-menu">
          <span
            className={`nav-item ${currentPage === 'dashboard' ? 'active' : ''}`}
            onClick={() => setCurrentPage('dashboard')}
          >
            Dashboard
          </span>
          <span
            className={`nav-item ${currentPage === 'failure' ? 'active' : ''}`}
            onClick={() => setCurrentPage('failure')}
          >
            Failure Risk
          </span>
          <span
            className={`nav-item ${currentPage === 'coverage' ? 'active' : ''}`}
            onClick={() => setCurrentPage('coverage')}
          >
            Coverage
          </span>
          <span
            className={`nav-item ${currentPage === 'debug' ? 'active' : ''}`}
            onClick={() => setCurrentPage('debug')}
          >
            Debug Queue
          </span>
          <span
            className={`nav-item ${currentPage === 'settings' ? 'active' : ''}`}
            onClick={() => setCurrentPage('settings')}
          >
            Settings
          </span>
        </div>
      </nav>

      {/* Main Content */}
      {renderPage()}
    </div>
  );
}

export default App;
