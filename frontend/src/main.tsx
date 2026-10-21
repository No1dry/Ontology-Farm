import { createRoot } from 'react-dom/client';
import { QueryClientProvider } from '@tanstack/react-query';
import { SessionBoundary } from './app/SessionBoundary';
import { sessionController } from './api/session';
import './styles/tokens.css';
import './styles/layout.css';
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={sessionController.queries}><SessionBoundary /></QueryClientProvider>);
