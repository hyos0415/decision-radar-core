# Elasticsearch + 한국어 형태소 분석기(nori). 버전은 compose와 같이 고정한다.
ARG ES_VERSION=9.5.4
FROM docker.elastic.co/elasticsearch/elasticsearch:${ES_VERSION}
RUN bin/elasticsearch-plugin install --batch analysis-nori
