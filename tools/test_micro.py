import speech_recognition as sr

def test_micro():
    recognizer = sr.Recognizer()
    
    # Imprime los dispositivos de audio que detecta Docker
    print("Dispositivos de audio detectados:")
    for index, name in enumerate(sr.Microphone.list_microphone_names()):
        print(f"[{index}] {name}")
    print("-" * 30)

    try:
        with sr.Microphone() as source:
            print("Ajustando ruido de fondo... un segundo.")
            recognizer.adjust_for_ambient_noise(source, duration=1)
            print("Escuchando... ¡Di algo!")
            
            # Escucha con un límite de tiempo para no quedarse colgado
            audio = recognizer.listen(source, timeout=5, phrase_time_limit=5)
            print("Procesando audio...")
            
            # Reconocimiento en español
            texto = recognizer.recognize_google(audio, language="es-ES")
            print(f"\nÉXITO. Has dicho: '{texto}'\n")
            
    except sr.WaitTimeoutError:
        print("Error: No he escuchado nada (timeout).")
    except sr.UnknownValueError:
        print("Error: No he entendido el audio (ruido o silencio).")
    except sr.RequestError as e:
        print(f"Error de red con la API de Google: {e}")
    except Exception as e:
        print(f"Error grave de hardware/PyAudio: {e}")

if __name__ == "__main__":
    test_micro()
