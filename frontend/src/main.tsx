import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import ProjectWorkspace from './ProjectWorkspace';
import LegacyHistory from './LegacyHistory';
import { browserConnection, WebApi } from './api';
import './theme.css';
import './global.css';

const connection = browserConnection();
const api = new WebApi(connection.token, connection.scopeId, connection.instanceId);
createRoot(document.getElementById('root')!).render(<StrictMode>{connection.scopeId === 'legacy'
  ? <LegacyHistory connection={connection} /> : <ProjectWorkspace api={api} />}</StrictMode>);
