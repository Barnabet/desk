import { useEffect } from 'react';
import { onPush } from './bridge';
import { CommandPalette } from './components/CommandPalette';
import { ErrorBoundary } from './components/ErrorBoundary';
import { ConnectionOverlay } from './components/ConnectionOverlay';
import { ProjectNav } from './components/ProjectNav';
import { TitleBar } from './components/TitleBar';
import { Toaster } from './components/Toast';
import { navigate, useRoute, type Route } from './router';
import { AttentionScreen } from './attention/AttentionScreen';
import { ConversationScreen } from './conversation/ConversationScreen';
import { ProjectFrame } from './conversation/ProjectFrame';
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

/** Which screen a route shows: a crashed screen resets when this changes, not on in-screen navigation. */
export function screenKey(route: Route): string {
  if (route.name === 'project') return `project/${route.id}/${route.tab}`;
  return route.name === 'catalog' ? 'skills' : route.name;
}

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
      if (route.tab === 'conversation') return <ConversationScreen key={route.id} projectId={route.id} {...(route.at !== undefined ? { at: route.at } : {})} />;
      if (route.tab === 'library') return <LibraryScreen key={route.id} projectId={route.id} {...(route.file ? { file: route.file } : {})} />;
      if (route.tab === 'settings') return <SettingsScreen key={route.id} projectId={route.id} />;
      if (route.tab === 'memory') return <MemoryScreen key={route.id} projectId={route.id} {...(route.q ? { q: route.q } : {})} />;
      return <ThreadsScreen key={route.id} projectId={route.id} {...(route.threadId ? { threadId: route.threadId } : {})} {...(route.at !== undefined ? { at: route.at } : {})} />;
    default:
      return null;
  }
}

export function App() {
  return (
    <ErrorBoundary scope="whole">
      <Shell />
    </ErrorBoundary>
  );
}

function Shell() {
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
        {route.name === 'project' && (route.tab === 'conversation' || route.tab === 'threads') ? (
          // One frame for both tabs, outside the screen's boundary, so switching tabs folds or unfolds its timeline.
          <ProjectFrame key={route.id} projectId={route.id} mode={route.tab === 'threads' ? 'full' : 'desk'} {...(route.threadId ? { focus: route.threadId } : {})}>
            <ErrorBoundary key={screenKey(route)}>
              <Screen route={route} />
            </ErrorBoundary>
          </ProjectFrame>
        ) : (
          <ErrorBoundary key={screenKey(route)}>
            <Screen route={route} />
          </ErrorBoundary>
        )}
        <ConnectionOverlay />
      </main>
      <CommandPalette />
      <Toaster />
    </div>
  );
}
