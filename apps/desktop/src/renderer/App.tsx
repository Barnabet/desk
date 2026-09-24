import { useEffect } from 'react';
import { onPush } from './bridge';
import { CommandPalette } from './components/CommandPalette';
import { ConnectionOverlay } from './components/ConnectionOverlay';
import { ProjectNav } from './components/ProjectNav';
import { TitleBar } from './components/TitleBar';
import { Toaster } from './components/Toast';
import { navigate, useRoute, type Route } from './router';
import { AttentionScreen } from './attention/AttentionScreen';
import { ConversationScreen } from './conversation/ConversationScreen';
import { LibraryScreen } from './knowledge/LibraryScreen';
import { MemoryScreen } from './knowledge/MemoryScreen';
import { MapScreen } from './map/MapScreen';
import { Onboarding, isOnboarded } from './screens/Onboarding';
import { SettingsScreen } from './settings/SettingsScreen';
import { SkillsScreen } from './skills/SkillsScreen';
import { SystemScreen } from './system/SystemScreen';
import { ThreadsScreen } from './threads/ThreadsScreen';
import { TrayPopover } from './tray/TrayPopover';
import { startGlobalSync } from './state/global';
import { startSessionRouting } from './state/session';

function Screen({ route }: { route: Route }) {
  switch (route.name) {
    case 'map':
      return <MapScreen newProject={route.newProject ?? false} />;
    case 'attention':
      return <AttentionScreen {...(route.item ? { itemId: route.item } : {})} />;
    case 'skills':
      return <SkillsScreen {...(route.skill ? { skill: route.skill } : {})} />;
    case 'catalog':
      return <SkillsScreen catalog {...(route.review ? { review: route.review } : {})} />;
    case 'system':
      return <SystemScreen />;
    case 'project':
      if (route.tab === 'conversation') return <ConversationScreen key={route.id} projectId={route.id} />;
      if (route.tab === 'library') return <LibraryScreen key={route.id} projectId={route.id} {...(route.file ? { file: route.file } : {})} />;
      if (route.tab === 'settings') return <SettingsScreen key={route.id} projectId={route.id} />;
      if (route.tab === 'memory') return <MemoryScreen key={route.id} projectId={route.id} {...(route.q ? { q: route.q } : {})} />;
      return <ThreadsScreen key={route.id} projectId={route.id} {...(route.threadId ? { threadId: route.threadId } : {})} />;
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
    if (!isOnboarded() && route.name !== 'onboarding' && route.name !== 'tray') navigate({ name: 'onboarding' });
  }, [route.name]);

  if (route.name === 'tray') return <TrayPopover />;
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
      <CommandPalette />
      <Toaster />
    </div>
  );
}
