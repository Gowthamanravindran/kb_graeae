import React from 'react';
import { Route, Switch } from 'react-router-dom';
import TaskCreation from './tasks/TaskCreation';
import { TASKS_CREATE_URL } from './appRoutes';

export const App = () => (
  <Switch>
    <Route path={TASKS_CREATE_URL} component={TaskCreation} />
    <Route path='/missing' component={NotImported} />
  </Switch>
);
