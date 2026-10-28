import { userSignUpJob } from './user';

export function start(JobsQueue, topic, makeModuleProcessor) {
  JobsQueue.process('SIGN_UP', userSignUpJob);
  JobsQueue.process(topic, makeModuleProcessor('entity'));
}
