import axios from 'axios';

let API_BASE_URL = process.env.REACT_APP_API_URL || 'http://127.0.0.1:8000';

export const setApiBaseUrl = (url) => {
  API_BASE_URL = url;
};

export const getApiBaseUrl = () => API_BASE_URL;

const getApi = () => axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Health check
export const healthCheck = async () => {
  try {
    const response = await getApi().get('/health');
    return response.data;
  } catch (error) {
    console.error('Health check failed:', error);
    throw error;
  }
};

// Get verification context for a run
export const getContext = async (runId) => {
  try {
    const response = await getApi().get(`/context/${runId}`);
    return response.data;
  } catch (error) {
    console.error('Error fetching context:', error);
    throw error;
  }
};

// Get failure predictions
export const getFailurePredictions = async (runId, featureRunId, topK = 200) => {
  try {
    const response = await getApi().post('/predict/failures', {
      run_id: runId,
      feature_run_id: featureRunId,
      top_k: topK,
    });
    return response.data;
  } catch (error) {
    console.error('Error fetching failure predictions:', error);
    throw error;
  }
};

// Get coverage priorities
export const getCoveragePriorities = async (runId, featureRunId, topK = 200) => {
  try {
    const response = await getApi().post('/prioritize/coverage', {
      run_id: runId,
      feature_run_id: featureRunId,
      top_k: topK,
    });
    return response.data;
  } catch (error) {
    console.error('Error fetching coverage priorities:', error);
    throw error;
  }
};

// Get debug priorities
export const getDebugPriorities = async (runId, featureRunId, topK = 500) => {
  try {
    const response = await getApi().post('/prioritize/debug', {
      run_id: runId,
      feature_run_id: featureRunId,
      top_k: topK,
    });
    return response.data;
  } catch (error) {
    console.error('Error fetching debug priorities:', error);
    throw error;
  }
};

// Generate verification plan
export const generateVerificationPlan = async (runId, topKEach = 10) => {
  try {
    const response = await getApi().post('/generate/verification-plan', {
      run_id: runId,
      top_k_each: topKEach,
    });
    return response.data;
  } catch (error) {
    console.error('Error generating verification plan:', error);
    throw error;
  }
};

// Get explanations for an item
export const getExplanation = async (itemId, runId = null, analysisType = null) => {
  try {
    let url = `/explanations/${encodeURIComponent(itemId)}`;
    const params = new URLSearchParams();
    if (runId) params.append('run_id', runId);
    if (analysisType) params.append('analysis_type', analysisType);
    if (params.toString()) url += `?${params.toString()}`;
    
    const response = await getApi().get(url);
    return response.data;
  } catch (error) {
    console.error('Error fetching explanation:', error);
    throw error;
  }
};

// Upload artifacts and run pipeline
export const uploadArtifacts = async (files, contextRunId, featureRunId, runPipeline = true) => {
  try {
    const formData = new FormData();
    files.forEach((file) => {
      formData.append('files', file);
    });
    formData.append('context_run_id', contextRunId);
    formData.append('feature_run_id', featureRunId);
    formData.append('run_pipeline', runPipeline.toString());
    formData.append('encoder', 'tfidf');
    formData.append('isolate_db', 'false');

    const response = await axios.post(`${API_BASE_URL}/ingest/logs`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      timeout: 1800000, // 30 minutes for long pipeline runs
    });
    return response.data;
  } catch (error) {
    console.error('Error uploading artifacts:', error);
    throw error;
  }
};

// Ingest artifacts from repos (clone and fetch metadata)
export const ingestArtifacts = async (mode = 'all', repos = null, skipCloneIfExists = true) => {
  try {
    const response = await getApi().post('/ingest/artifacts', {
      mode: mode,
      repos: repos,
      skip_clone_if_exists: skipCloneIfExists,
      max_commits: 250,
      max_issues: 250,
    });
    return response.data;
  } catch (error) {
    console.error('Error ingesting artifacts:', error);
    throw error;
  }
};

export default { getApiBaseUrl, setApiBaseUrl };
