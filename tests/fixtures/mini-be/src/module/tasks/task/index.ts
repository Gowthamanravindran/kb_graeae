import { TaskTC } from './types';

export const mutation = {
  AddTask: TaskTC.getResolver('AddTask'),
};
