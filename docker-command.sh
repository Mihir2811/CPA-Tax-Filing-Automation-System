sudo docker stop $(sudo -S docker ps -q  --filter ancestor=virtue-be)
sudo docker build -t virtue-be .
sudo docker run -d --restart=on-failure:5 --network=host -v $(pwd):/code virtue-be
