import { gql } from '@apollo/client';

export const addTaskQuery = gql`
  mutation AddTask($input: AddTaskTypeInput) {
    AddTask(input: $input) {
      eid
    }
  }
`;

export const taskByIdQuery = gql`
  query TaskById($eid: String!) {
    TaskById(eid: $eid) {
      eid
    }
  }
`;
