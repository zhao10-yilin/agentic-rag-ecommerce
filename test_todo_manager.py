import json
import os
import tempfile
import unittest
from todo_manager import TodoManager


class TestTodoManager(unittest.TestCase):
    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json')
        self.temp_file.close()
        self.manager = TodoManager(self.temp_file.name)

    def tearDown(self):
        if os.path.exists(self.temp_file.name):
            os.remove(self.temp_file.name)

    def test_add_task(self):
        task_id = self.manager.add("买牛奶")
        self.assertEqual(task_id, 1)
        tasks = self.manager.list()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["content"], "买牛奶")
        self.assertFalse(tasks[0]["done"])

    def test_add_multiple_tasks(self):
        self.manager.add("任务1")
        self.manager.add("任务2")
        tasks = self.manager.list()
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["id"], 1)
        self.assertEqual(tasks[1]["id"], 2)

    def test_list_shows_only_incomplete(self):
        self.manager.add("未完成任务")
        self.manager.add("已完成任务")
        self.manager.done(2)
        tasks = self.manager.list()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["content"], "未完成任务")

    def test_done_task(self):
        self.manager.add("任务")
        result = self.manager.done(1)
        self.assertTrue(result)
        tasks = self.manager.list()
        self.assertEqual(len(tasks), 0)

    def test_done_nonexistent_task(self):
        self.manager.add("任务")
        result = self.manager.done(999)
        self.assertFalse(result)

    def test_delete_task(self):
        self.manager.add("任务")
        result = self.manager.delete(1)
        self.assertTrue(result)
        tasks = self.manager.list()
        self.assertEqual(len(tasks), 0)

    def test_delete_nonexistent_task(self):
        self.manager.add("任务")
        result = self.manager.delete(999)
        self.assertFalse(result)

    def test_persistence(self):
        self.manager.add("持久化任务")
        self.manager.done(1)

        manager2 = TodoManager(self.temp_file.name)
        tasks = manager2.list()
        self.assertEqual(len(tasks), 0)

        all_data = manager2._load()
        self.assertEqual(len(all_data), 1)
        self.assertEqual(all_data[0]["content"], "持久化任务")
        self.assertTrue(all_data[0]["done"])

    def test_empty_list(self):
        tasks = self.manager.list()
        self.assertEqual(tasks, [])

    def test_id_increments_correctly_after_delete(self):
        self.manager.add("任务1")
        self.manager.add("任务2")
        self.manager.delete(1)
        task_id = self.manager.add("任务3")
        self.assertEqual(task_id, 3)


class TestTodoManagerCLI(unittest.TestCase):
    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json')
        self.temp_file.close()
        self.manager = TodoManager(self.temp_file.name)

    def tearDown(self):
        if os.path.exists(self.temp_file.name):
            os.remove(self.temp_file.name)

    def test_cli_add(self):
        import io
        from unittest.mock import patch
        from todo_manager import main

        test_args = ['todo_manager.py', 'add', '测试任务']
        with patch('sys.argv', test_args):
            with patch('sys.stdout', new_callable=io.StringIO) as mock_stdout:
                main(data_file=self.temp_file.name)
                output = mock_stdout.getvalue()
                self.assertIn("已添加任务", output)

    def test_cli_list(self):
        import io
        from unittest.mock import patch
        from todo_manager import main

        self.manager.add("任务A")
        self.manager.add("任务B")
        test_args = ['todo_manager.py', 'list']
        with patch('sys.argv', test_args):
            with patch('sys.stdout', new_callable=io.StringIO) as mock_stdout:
                main(data_file=self.temp_file.name)
                output = mock_stdout.getvalue()
                self.assertIn("任务A", output)
                self.assertIn("任务B", output)

    def test_cli_done(self):
        import io
        from unittest.mock import patch
        from todo_manager import main

        self.manager.add("任务")
        test_args = ['todo_manager.py', 'done', '1']
        with patch('sys.argv', test_args):
            with patch('sys.stdout', new_callable=io.StringIO) as mock_stdout:
                main(data_file=self.temp_file.name)
                output = mock_stdout.getvalue()
                self.assertIn("已标记完成", output)

    def test_cli_delete(self):
        import io
        from unittest.mock import patch
        from todo_manager import main

        self.manager.add("任务")
        test_args = ['todo_manager.py', 'delete', '1']
        with patch('sys.argv', test_args):
            with patch('sys.stdout', new_callable=io.StringIO) as mock_stdout:
                main(data_file=self.temp_file.name)
                output = mock_stdout.getvalue()
                self.assertIn("已删除任务", output)


if __name__ == '__main__':
    unittest.main()
