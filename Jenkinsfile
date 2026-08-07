pipeline {
    // Release builds are portable across the Jenkins fleet; do not reserve the
    // two heavy agents while compatible capacity is available elsewhere.
    agent any

    options {
        ansiColor('xterm')
        timeout(time: 180, unit: 'MINUTES')
    }

    environment {
        CI = 'true'
        HOME = "${WORKSPACE}"
    }

    stages {
        stage('Setup') {
            steps {
                sshagent(credentials: ['github-ssh']) {
                    sh 'git submodule update --init --recursive'
                }
                sh 'make setup'
            }
        }

        stage('Check') {
            steps {
                sh 'make check'
            }
        }

        stage('Build release artifacts') {
            steps {
                sh 'make build'
                sh '''#!/usr/bin/env bash
                    set -euo pipefail
                    version=$(< VERSION)
                    expected=(
                      "dist/gauntlet-${version}.AppImage"
                      "dist/gauntlet-${version}.deb"
                      "dist/gauntlet-${version}-image.tar.gz"
                      "dist/gauntlet-${version}-py3-none-any.whl"
                      "dist/gauntlet_sdk-${version}-py3-none-any.whl"
                      "dist/README.txt"
                      "dist/setup-host.sh"
                      "dist/99-gauntlet-instruments.rules"
                    )
                    for artifact in "${expected[@]}"; do
                      test -s "$artifact"
                    done
                    test "$(find dist -maxdepth 1 -type f | wc -l)" -eq "${#expected[@]}"
                    printf 'Verified release artifacts:\n'
                    printf '  %s\n' "${expected[@]}"
                '''
            }
        }
    }

    post {
        always {
            archiveArtifacts artifacts: 'dist/*', allowEmptyArchive: true, fingerprint: true
            junit allowEmptyResults: true, testResults: 'build/junit.xml'
            cleanWs(deleteDirs: true, patterns: [[pattern: '.git/**', type: 'EXCLUDE']])
        }
        regression {
            slackSend(
                color: 'danger',
                message: ":red_circle: *${env.JOB_NAME}* #${env.BUILD_NUMBER} - FAILURE\n<${env.BUILD_URL}|View Build>",
                notifyCommitters: true
            )
        }
        fixed {
            slackSend(
                color: 'good',
                message: ":large_green_circle: *${env.JOB_NAME}* #${env.BUILD_NUMBER} - FIXED\n<${env.BUILD_URL}|View Build>",
                notifyCommitters: true
            )
        }
    }
}
