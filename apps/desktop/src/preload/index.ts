import { contextBridge } from 'electron';

contextBridge.exposeInMainWorld('desk', {});
