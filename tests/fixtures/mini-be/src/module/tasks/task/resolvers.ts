import { TaskModel } from './model';
import { getTaskQueue, TaskTopics } from '../../../queue/task/main';

const TaskQueue = getTaskQueue();

const AddTask = {
  name: 'AddTask',
  type: 'Task',
  resolve: async ({ args }) => {
    const task = await TaskModel.create(args.input);
    if (task.repeat) {
      scheduleRepeat(task);
    }
    return task;
  },
};

function scheduleRepeat(task) {
  TaskQueue.add(TaskTopics.TASK_REPEAT, { taskId: task._id }, { delay: 1000 });
}

export { AddTask };
