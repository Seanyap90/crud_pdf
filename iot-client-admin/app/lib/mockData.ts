import type { Gateway, InsertGateway } from "@shared/schema";

function generateUUID(): string {
  // This is a very basic UUID generation for browser compatibility.  A more robust library should be used in production.
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
    var r = Math.random() * 16 | 0, v = c == 'x' ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
}

// In-memory data store for mock data
const gateways: Gateway[] = [
  {
    id: generateUUID(),
    name: "Gateway 1",
    location: "Building A",
    status: "online",
    lastSeen: new Date().toISOString(),
    deviceCount: 5
  },
  {
    id: generateUUID(),
    name: "Gateway 2",
    location: "Building B",
    status: "offline",
    lastSeen: new Date(Date.now() - 86400000).toISOString(),
    deviceCount: 3
  },
  {
    id: generateUUID(),
    name: "Gateway 3",
    location: "Building C",
    status: "warning",
    lastSeen: new Date().toISOString(),
    deviceCount: 8
  }
];

export const mockDataService = {
  listGateways: (): Promise<Gateway[]> => {
    return Promise.resolve(gateways);
  },
  
  getGateway: (id: string): Promise<Gateway | undefined> => {
    const gateway = gateways.find(g => g.id === id);
    return Promise.resolve(gateway);
  },
  
  createGateway: (data: InsertGateway): Promise<Gateway> => {
    const newGateway: Gateway = {
      id: generateUUID(),
      ...data,
      lastSeen: new Date().toISOString(),
      deviceCount: 0
    };
    
    gateways.push(newGateway);
    return Promise.resolve(newGateway);
  },
  
  updateGateway: (id: string, data: Partial<Gateway>): Promise<Gateway | undefined> => {
    const index = gateways.findIndex(g => g.id === id);
    if (index === -1) return Promise.resolve(undefined);
    
    gateways[index] = { ...gateways[index], ...data };
    return Promise.resolve(gateways[index]);
  },
  
  deleteGateway: (id: string): Promise<boolean> => {
    const index = gateways.findIndex(g => g.id === id);
    if (index === -1) return Promise.resolve(false);
    
    gateways.splice(index, 1);
    return Promise.resolve(true);
  }
};

