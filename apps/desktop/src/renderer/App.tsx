import { useEffect } from 'react';
import { onPush } from './bridge';
import { ConnectionOverlay } from './components/ConnectionOverlay';
import { ProjectNav } from './components/ProjectNav';
import { TitleBar } from './components/TitleBar';
import { Toaster } from './components/Toast';
import { navigate, useRoute, type Route } from './router';
import { ConversationScreen } from './conversation/ConversationScreen';
import { MapScreen } from './map/MapScreen';
import { Onboarding, isOnboarded } from './screens/Onboarding';
import { Pending } from './screens/Pending';
import { ThreadsScreen } from './threads/ThreadsScreen';
import { startGlobalSync } from './state/global';
import { startSessionRouting } from './state/session';

function Screen({ route }: { route: Route }) {
  switch (route.name) {
    case 'map':
      return <MapScreen newProject={route.newProject ?? false} />;
    case 'attention':
      return <Pending title="Attention" />;
    case 'skills':
      return <Pending title="Skills" />;
    case 'system':
      return <Pending title="System" />;
    case 'project':
      if (route.tab === 'conversation') return <ConversationScreen key={route.id} projectId={route.id} />;
      if (route.tab === 'threads') return <ThreadsScreen key={route.id} projectId={route.id} {...(route.threadId ? { threadId: route.threadId } : {})} />;
      return <Pending title={route.tab[0]!.toUpperCase() + route.tab.slice(1)} />;
    default:
      return null;
  }
}

export function App() {
  const route = useRoute();
  useEffect(() => startGlobalSync(), []);
  useEffect(() => startSessionRouting(), []);
  useEffect(() => onPush<string>('desk:navigate', (r) => navigate(r)), []);
  useEffect(() => {
    if (!isOnboarded() && route.name !== 'onboarding') navigate({ name: 'onboarding' });
  }, [route.name]);

  if (route.name === 'onboarding') {
    return (
      <>
        <Onboarding />
        <Toaster />
      </>
    );
  }
  return (
    <div className="app">
      <TitleBar route={route} />
      {route.name === 'project' ? <ProjectNav projectId={route.id} tab={route.tab} /> : null}
      <main className="screen">
        <Screen route={route} />
        <ConnectionOverlay />
      </main>
      <Toaster />
    </div>
  );
}
