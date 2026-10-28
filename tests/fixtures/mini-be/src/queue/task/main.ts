export const TaskTopics = { TASK_REPEAT: 'TASK_REPEAT' };

export function getTaskQueue() {
  return makeQueue('task');
}

export function requeue(task) {
  const TaskQueue = getTaskQueue();
  TaskQueue.add(TaskTopics.TASK_REPEAT, { taskId: task._id });
}
