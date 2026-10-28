export async function userSignUpJob(job) {
  return job.data;
}

export function enqueueSignUp(UserQueue, user) {
  UserQueue.add('SIGN_UP', { id: user.id });
}
