import React from 'react';
import { useMutation } from '@apollo/client';
import { addTaskQuery } from './task.graphql';

const TaskCreation = () => {
  const [submitTask] = useMutation(addTaskQuery);
  return <button onClick={() => submitTask({ variables: { input: {} } })}>Publish</button>;
};

export default TaskCreation;
