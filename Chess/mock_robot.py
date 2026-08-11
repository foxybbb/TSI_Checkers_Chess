import socket
import time

def test_robot():
    host = '127.0.0.1' # локалхост
    port = 3000

    print("Подключаемся к игре...")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((host, port))
        print("Подключено! Ждем 3 секунды...")
        time.sleep(3)
        
        # Эмулируем ход белых
        print("Отправляем команду MOVE...")
        s.sendall(b"MOVE\n")
        
        # Слушаем ответ от ИИ
        print("Ждем ход ИИ (черных)...")
        data = s.recv(1024)
        print(f"Получен ответ: {data} -> как строка: {data.decode('ascii')}")

if __name__ == "__main__":
    test_robot()